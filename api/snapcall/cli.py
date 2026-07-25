"""Command line entry points.

    uv run python -m snapcall.cli preflight       # is the key alive, do we have credits
    uv run python -m snapcall.cli emergency       # caregiver alert + escalation
    uv run python -m snapcall.cli errand          # prescription refill
    uv run python -m snapcall.cli raw --to +1... --brief "..."

Everything is DRY RUN unless you pass --live. Dry run simulates the full event
stream including a mid-call question, so the state machine is rehearsable
without credits or a ringing phone.
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import answerer as ans
from . import config, demo_data, flows
from .callwright import CallEvent, CallResult, CallwrightClient, CallwrightError


def _log_setup() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")


def _print_event(event: CallEvent) -> None:
    if event.type == "status":
        print(f"    [{event.data.get('status')}]")
    elif event.type == "transcript":
        who = event.data.get("speaker", "?")
        print(f"    {who}: {event.data.get('text', '')}")
    elif event.type == "outcome":
        print(f"    == {event.data.get('outcome_type')} ==")


def _build_answerer(use_llm: bool):
    rungs = [
        ans.live_context_rung(demo_data.get_live_context),
        ans.profile_rung(demo_data.PROFILE),
    ]
    if use_llm:
        rungs.append(ans.llm_rung(demo_data.context_pack()))
    return ans.chain(*rungs)


def _report(result: CallResult) -> None:
    print("\n" + "=" * 64)
    print(f"outcome:  {result.outcome_type}   (reached={result.reached}, billed={result.billed_credits})")
    print(f"summary:  {result.summary}")
    print("-" * 64)
    print(result.transcript or "(no transcript)")
    print("=" * 64)


def cmd_preflight(client: CallwrightClient, args) -> int:
    print(f"base url:  {client.base_url}")
    print(f"api key:   {config.redact(config.API_KEY) if config.API_KEY else '(not set)'}")
    print(f"dry run:   {client.dry_run}")
    print(f"allowlist: {', '.join(config.ALLOWED_NUMBERS) or '(disabled — any number is dialable!)'}")

    if not config.API_KEY:
        print("\nFAIL: no CALLWRIGHT_API_KEY. cp .env.example .env and paste the team key.")
        return 1
    try:
        print(f"health:    {client.health()}")
    except CallwrightError as exc:
        print(f"health:    FAILED {exc}")
    try:
        print(f"whoami:    {client.me()}")
    except CallwrightError as exc:
        print(f"whoami:    FAILED {exc}")
    try:
        ok, message = client.can_dial()
        print(f"credits:   {'OK' if ok else 'BLOCKED'} — {message}")
        return 0 if ok else 1
    except CallwrightError as exc:
        print(f"credits:   FAILED {exc}")
        return 1


def cmd_emergency(client: CallwrightClient, args) -> int:
    contacts = [
        flows.Contact("Sarah", args.to or config.CAREGIVER_PRIMARY_PHONE, "daughter"),
        flows.Contact("David", config.CAREGIVER_BACKUP_PHONE, "neighbor"),
    ]
    contacts = [c for c in contacts if c.phone]
    if not contacts:
        print("No caregiver numbers. Set CAREGIVER_PRIMARY_PHONE in .env or pass --to.")
        return 1

    report = flows.emergency(
        client,
        contacts,
        person_name=demo_data.PERSON_NAME,
        callback_number=demo_data.PROFILE["phone"],
        known_facts=demo_data.trigger_facts(),
        answerer=_build_answerer(args.llm),
        on_event=_print_event,
    )
    for result in report.attempts:
        _report(result)
    if report.reached_via:
        print(f"\nREACHED {report.reached_via.name} ({report.reached_via.relationship})")
    else:
        print(f"\nNOBODY REACHED after {len(report.attempts)} attempt(s). SMS fallback -> {report.sms_sent_to or '(not wired)'}")
    return 0


def _make_hub(client: CallwrightClient, args):
    from . import trigger

    contacts = [
        trigger.flows.Contact("Sarah", args.to or config.CAREGIVER_PRIMARY_PHONE, "daughter"),
        trigger.flows.Contact("David", config.CAREGIVER_BACKUP_PHONE, "neighbor"),
    ]
    contacts = [c for c in contacts if c.phone]
    if not contacts:
        return None
    return trigger.TriggerHub(client, contacts, answerer=_build_answerer(args.llm))


def cmd_serve(client: CallwrightClient, args) -> int:
    """Run the endpoint the band posts into."""
    from . import server

    hub = _make_hub(client, args)
    if hub is None:
        print("No caregiver numbers. Set CAREGIVER_PRIMARY_PHONE in .env or pass --to.")
        return 1
    print(f"dashboard:  http://localhost:{args.port}")
    print(f"fire it:    curl -X POST http://localhost:{args.port}/trigger")
    print(f"add scene:  curl -X POST http://localhost:{args.port}/context "
          f"-d '{{\"scene\":\"she is on the kitchen floor\"}}'\n")
    try:
        server.serve(hub, port=args.port)
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def cmd_listen(client: CallwrightClient, args) -> int:
    """Keyboard stands in for the wristband — press Enter to snap."""
    hub = _make_hub(client, args)
    if hub is None:
        print("No caregiver numbers. Set CAREGIVER_PRIMARY_PHONE in .env or pass --to.")
        return 1
    print("Press ENTER to snap.  Press ENTER again within 3s to cancel.  Ctrl-C to quit.\n")
    try:
        while True:
            input()
            print(f"  -> {hub.snap(source='keyboard')}")
    except (KeyboardInterrupt, EOFError):
        print("\nstopped")
    return 0


def cmd_errand(client: CallwrightClient, args) -> int:
    phone = args.to or config.ERRAND_PHONE
    if not phone:
        print("No target. Set ERRAND_PHONE in .env or pass --to.")
        return 1
    result = flows.errand(
        client,
        business_name=args.business,
        business_phone=phone,
        person_name=demo_data.PERSON_NAME,
        task=args.task,
        callback_number=demo_data.PROFILE["phone"],
        known_details={
            "Date of birth": demo_data.PROFILE["date_of_birth"],
            "Prescription number": demo_data.PROFILE["prescription_number"],
            "Insurance": demo_data.PROFILE["member_id"],
        },
        answerer=_build_answerer(args.llm),
        on_event=_print_event,
    )
    _report(result)
    return 0 if result.reached else 2


def cmd_raw(client: CallwrightClient, args) -> int:
    if not args.to or not args.brief:
        print("raw needs --to and --brief")
        return 1
    result = client.call(args.to, args.brief, answerer=_build_answerer(args.llm), on_event=_print_event)
    _report(result)
    return 0 if result.reached else 2


def main(argv: list[str] | None = None) -> int:
    _log_setup()
    # Shared flags, attached to both the top level and every subcommand, so
    # `snapcall --live emergency` and `snapcall emergency --live` both work.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--live", action="store_true", help="actually dial. Without this, nothing rings.")
    common.add_argument("--llm", action="store_true", help="add the LLM rung to the answerer chain")
    common.add_argument("--to", help="target phone, E.164")
    common.add_argument(
        "--camera",
        action="store_true",
        help="simulate the ESP32 camera having captioned the scene. Without it the agent "
        "knows only that she asked for help — which is the truth for a bare snap.",
    )

    parser = argparse.ArgumentParser(prog="snapcall", parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("preflight", help="check key, credits, connectivity", parents=[common])
    sub.add_parser("emergency", help="caregiver alert with escalation", parents=[common])
    sub.add_parser("listen", help="keyboard stands in for the band — ENTER to snap", parents=[common])

    serve = sub.add_parser("serve", help="HTTP endpoint the band posts snaps into", parents=[common])
    serve.add_argument("--port", type=int, default=8787)

    errand = sub.add_parser("errand", help="run an errand call", parents=[common])
    errand.add_argument("--business", default="Walgreens pharmacy")
    errand.add_argument(
        "--task",
        default=(
            "Refill her Lisinopril prescription, RX 7741903, for pickup at the 16th and Mission "
            "store. Find out when it will be ready and whether anything is needed from her doctor."
        ),
    )

    raw = sub.add_parser("raw", help="place an arbitrary call", parents=[common])
    raw.add_argument("--brief", help="the full plain-English task")

    args = parser.parse_args(argv)
    for name in ("business", "task", "brief"):
        if not hasattr(args, name):
            setattr(args, name, None)
    if not hasattr(args, "port"):
        args.port = 8787

    if args.camera:
        demo_data.simulate_camera()
        print("camera context ON — the agent can describe the scene if asked.\n")

    dry_run = not args.live
    if not dry_run:
        print("!! LIVE MODE — a real phone will ring and credits will be spent.\n")
    client = CallwrightClient(dry_run=dry_run)

    handlers = {
        "preflight": cmd_preflight,
        "emergency": cmd_emergency,
        "errand": cmd_errand,
        "raw": cmd_raw,
        "serve": cmd_serve,
        "listen": cmd_listen,
    }
    try:
        return handlers[args.command](client, args)
    except (CallwrightError, config.ConfigError) as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
