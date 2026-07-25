"""Callwright client — place a real phone call, follow it live, classify the outcome.

Design notes (read these before changing anything):

* The public API docs and the vendored SKILL.md DISAGREE in two places, so this
  client is defensive about both:
    1. The freeform success envelope is documented as
       `{"call": {...}, "task_id", "credits_reserved", "owner_pod"}` (no
       top-level `call_id`), while SKILL.md says to grab a top-level `call_id`.
       `_extract_call_id` accepts either.
    2. SKILL.md documents `GET /calls/{id}/events` for the live feed, but that
       operation is absent from the published OpenAPI list. `follow()` probes it
       once and silently falls back to polling `GET /calls/{id}` if it 404s.
       Watch the log line — if the fallback engages you lose mid-call
       `ask_user`, which is a big deal for us.

* Outcome/summary/transcript populate 20-30s AFTER status flips to `completed`.
  So `follow()` returns as soon as the call ends (that is what drives the wrist
  buzz), and `result()` separately waits out the lag for the dashboard.

* Rate limits are 10 req/s and 100 req/min per key. One poll per second per
  call, with at most 2 concurrent calls, sits comfortably inside that.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

import requests

from . import config

log = logging.getLogger("snapcall.callwright")

# --- outcome taxonomy -------------------------------------------------------
# Billed 10 credits; a real conversation happened.
REACHED_OUTCOMES = {"success_booked", "success_refused", "success_no_booking"}
# Free; nobody was actually spoken to. ALL of these must trigger escalation.
NOT_REACHED_OUTCOMES = {
    "failed_short_hangup",  # most common: picked up, hung up after AI disclosure
    "failed_voicemail",
    "failed_no_answer",
    "failed_busy",
    "failed_no_agent_available",
    "failed_technical",
}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class CallwrightError(RuntimeError):
    """Base class. `.status` is the HTTP code when there was a response."""

    def __init__(self, message: str, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class InsufficientCredits(CallwrightError):
    """402. Note this fires whenever remaining < 200, not only at zero."""


class ConcurrencyLimit(CallwrightError):
    """409. Key is at its concurrent-call cap (typically 2)."""


class ValidationFailed(CallwrightError):
    """422. On the structured path, `.payload` lists the missing slots."""


# --- result types -----------------------------------------------------------


@dataclass
class CallEvent:
    """One event off the live feed."""

    id: int
    type: str  # e.g. "status", "ask_user", "transcript", "outcome"
    data: dict


@dataclass
class CallResult:
    call_id: str
    status: str
    outcome_type: str | None
    summary: str | None
    transcript: str | None
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def reached(self) -> bool:
        """True only if a human actually had a conversation with the agent.

        Escalation branches on this. Anything else — voicemail, hangup, busy,
        technical failure — means the message did not land.
        """
        return self.outcome_type in REACHED_OUTCOMES

    @property
    def billed_credits(self) -> int:
        return 10 if self.reached else 0


# An answerer turns a mid-call question into a spoken reply.
Answerer = Callable[[str], str]
EventHook = Callable[[CallEvent], None]


def _extract_call_id(payload: dict) -> str:
    """Pull the call id out of whichever envelope shape came back."""
    candidates: Iterable[Any] = (
        payload.get("call_id"),
        (payload.get("call") or {}).get("call_id"),
        (payload.get("call") or {}).get("id"),
        payload.get("id"),
    )
    for value in candidates:
        if isinstance(value, str) and value:
            return value
    raise CallwrightError(f"no call_id in response envelope: {json.dumps(payload)[:400]}", payload=payload)


def _format_transcript(raw: Any) -> str | None:
    """Normalise `transcript_full` into readable lines.

    The API returns a list of `{"role": "bot"|"operator", "text": ...}` turns,
    not the string the docs imply. Kept tolerant in case that changes.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        labels = {"bot": "Agent", "operator": "Them", "user": "Them", "assistant": "Agent"}
        lines = []
        for turn in raw:
            if not isinstance(turn, dict):
                lines.append(str(turn))
                continue
            role = str(turn.get("role", "?"))
            lines.append(f"{labels.get(role, role.capitalize()):>6}: {turn.get('text', '')}")
        return "\n".join(lines)
    return str(raw)


def _parse_sse(text: str) -> list[CallEvent]:
    """Parse an SSE body into events. Tolerates partial trailing blocks."""
    events: list[CallEvent] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event_id, event_type, data_lines = None, "message", []
        for line in block.splitlines():
            if line.startswith("id:"):
                raw = line[3:].strip()
                event_id = int(raw) if raw.isdigit() else None
            elif line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if event_id is None:
            continue
        raw_data = "\n".join(data_lines)
        try:
            data = json.loads(raw_data) if raw_data else {}
        except json.JSONDecodeError:
            data = {"raw": raw_data}
        events.append(CallEvent(id=event_id, type=event_type, data=data))
    return events


class CallwrightClient:
    """Thin, synchronous client. One instance is fine for the whole app."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None, dry_run: bool | None = None):
        self.base_url = (base_url or config.BASE_URL).rstrip("/")
        self.dry_run = config.DRY_RUN if dry_run is None else dry_run
        self._api_key = api_key or config.API_KEY
        if not self.dry_run:
            self._api_key = api_key or config.assert_key()
        self._session = requests.Session()
        self._session.headers.update({"X-API-Key": self._api_key, "Content-Type": "application/json"})
        # None = not probed yet, True/False = whether /events exists.
        self._events_supported: bool | None = None

    # --- plumbing ----------------------------------------------------------

    def _request(self, method: str, path: str, *, timeout: float = 20, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        response = self._session.request(method, url, timeout=timeout, **kwargs)
        if response.status_code < 400:
            return response
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text[:400]}
        detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
        message = f"{method} {path} -> {response.status_code}: {json.dumps(detail)[:400]}"
        if response.status_code == 402:
            raise InsufficientCredits(
                message + "  (each dial reserves 200 credits, so this fires whenever remaining < 200)",
                response.status_code,
                payload,
            )
        if response.status_code == 409:
            raise ConcurrencyLimit(message + "  (key is at its concurrent-call cap)", response.status_code, payload)
        if response.status_code == 422:
            raise ValidationFailed(message, response.status_code, payload)
        raise CallwrightError(message, response.status_code, payload)

    # --- account -----------------------------------------------------------

    def health(self) -> dict:
        return self._request("GET", "/health").json()

    def usage(self) -> dict:
        """-> {"remaining", "quota_limit", "current_usage"}"""
        return self._request("GET", "/v1/usage").json()

    def me(self) -> dict:
        return self._request("GET", "/users/me").json()

    def can_dial(self) -> tuple[bool, str]:
        """Check headroom for the 200-credit reservation before you're on stage."""
        usage = self.usage()
        remaining = usage.get("remaining", 0)
        if remaining < 200:
            return False, f"remaining={remaining} < 200 reservation floor — POST /calls will 402. Ask VOYGR to top up."
        return True, f"remaining={remaining} (~{remaining // 10} successful calls)"

    # --- placing -----------------------------------------------------------

    def place(self, target_phone: str, brief: str, language: str = "en") -> str:
        """Place a freeform call. Returns the call_id.

        `brief` is the ONLY thing the voice agent reads. There is no separate
        caller-name or script field — every name, number, date and wrap-up
        instruction has to be in this string.
        """
        target_phone = config.assert_dialable(target_phone)
        if not 10 <= len(brief) <= 4000:
            raise ValueError(f"brief must be 10-4000 chars, got {len(brief)}")

        body = {
            "target_phone": target_phone,
            "brief": brief,
            "language": language,
            # Required for mid-call questions to reach our poll loop. Without
            # it they go to legacy operator channels and we never see them.
            "ask_user_mode": "stream",
        }

        if self.dry_run:
            call_id = f"dry-{uuid.uuid4().hex[:12]}"
            log.warning("DRY RUN — no phone will ring. call_id=%s", call_id)
            log.info("would POST /calls: %s", json.dumps(body, indent=2))
            _DRY_RUNS[call_id] = _DryRunCall(target_phone=target_phone, brief=brief)
            return call_id

        payload = self._request("POST", "/calls", json=body).json()
        call_id = _extract_call_id(payload)
        log.info("dialing %s -> call_id=%s", target_phone, call_id)
        return call_id

    def answer(self, call_id: str, request_id: str, answer: str) -> None:
        """Reply to a mid-call question. The agent waits ~60s, then gives up."""
        if self.dry_run:
            log.info("DRY RUN answer -> %s", answer)
            return
        self._request("POST", f"/calls/{call_id}/answer", json={"request_id": request_id, "answer": answer})

    # --- following ---------------------------------------------------------

    def _fetch_events(self, call_id: str, after_event_id: int, window: float = 5.0) -> list[CallEvent]:
        """Read up to `window` seconds off the SSE feed, then return what arrived.

        This MUST stream. `/calls/{id}/events` is a live SSE endpoint that holds
        the connection open, so a plain blocking GET read-timeouts and requests
        throws the buffered body away with it. `curl --max-time` returns the
        partial body instead, which is why the documented curl loop works and a
        naive port of it does not. Read lines incrementally and treat the
        timeout as "nothing more for now" rather than as an error.
        """
        # Query param, not the Last-Event-ID header — the gateway strips it.
        deadline = time.monotonic() + window
        lines: list[str] = []
        try:
            with self._session.get(
                f"{self.base_url}/calls/{call_id}/events",
                params={"after_event_id": after_event_id},
                stream=True,
                timeout=(5, window),
            ) as response:
                if response.status_code == 404:
                    raise CallwrightError("events endpoint not available", 404)
                response.raise_for_status()
                for line in response.iter_lines(decode_unicode=True):
                    lines.append(line or "")
                    if time.monotonic() >= deadline:
                        break
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            # Expected: the stream had nothing more to say inside `window`.
            # requests re-wraps a urllib3 read-timeout raised mid-stream as
            # ConnectionError rather than Timeout, so BOTH have to be caught —
            # catching only Timeout lets it escape from iter_lines().
            # `lines` keeps whatever arrived before the stall.
            pass
        return _parse_sse("\n".join(lines))

    def follow(
        self,
        call_id: str,
        answerer: Answerer | None = None,
        on_event: EventHook | None = None,
        timeout: float = 300,
    ) -> str | None:
        """Block until the call ends. Returns outcome_type if the feed gave one.

        This returns the moment the call is over — use it to drive the wrist
        buzz and the live dashboard. Do NOT wait on `result()` for that; the
        transcript lags 20-30s behind.

        `answerer` is called with the agent's question and must return a spoken
        reply. It should ALWAYS return something — a truthful "I don't have that
        information" beats a 60s silence on a live call.
        """
        if self.dry_run:
            return _dry_run_follow(call_id, answerer, on_event)

        deadline = time.monotonic() + timeout
        last_event_id = 0
        answered: set[str] = set()

        if self._events_supported is None:
            # Probe with a short window. Only a genuine 404/HTTP error means the
            # feed is absent — an empty read just means the call hasn't started
            # emitting yet, which is normal a second after dialing.
            try:
                self._fetch_events(call_id, 0, window=3.0)
                self._events_supported = True
                log.info("live event feed available — mid-call questions ARE answerable")
            except (CallwrightError, requests.HTTPError) as exc:
                self._events_supported = False
                log.warning(
                    "no live event feed (%s) — falling back to status polling. "
                    "Mid-call questions will NOT be answerable.",
                    exc,
                )

        if not self._events_supported:
            return self._follow_by_polling(call_id, on_event, deadline)

        while time.monotonic() < deadline:
            try:
                events = self._fetch_events(call_id, last_event_id)
            except (requests.RequestException, CallwrightError) as exc:
                log.debug("event poll hiccup, retrying: %s", exc)
                time.sleep(1)
                continue

            for event in events:
                last_event_id = max(last_event_id, event.id)
                if on_event:
                    on_event(event)

                if event.type == "ask_user":
                    request_id = event.data.get("request_id")
                    question = event.data.get("message") or event.data.get("question") or ""
                    if not request_id or request_id in answered:
                        continue
                    answered.add(request_id)
                    reply = _safe_answer(answerer, question)
                    log.info("mid-call Q: %s\n            A: %s", question, reply)
                    self.answer(call_id, request_id, reply)

                elif event.type == "outcome":
                    outcome = event.data.get("outcome_type") or event.data.get("type")
                    log.info("call ended: %s", outcome)
                    return outcome

            # Belt and braces: the feed is not guaranteed to deliver an
            # `outcome` event, and without this check a finished call would sit
            # here until the 300s timeout with the demo frozen on screen. One
            # cheap status read per ~5s window, well inside the rate limit.
            status_payload = self._request("GET", f"/calls/{call_id}").json()
            if status_payload.get("status") in TERMINAL_STATUSES:
                outcome = status_payload.get("outcome_type")
                log.info("call ended (via status): %s", outcome)
                return outcome

            time.sleep(1)

        log.warning("follow() timed out after %ss", timeout)
        return None

    def _follow_by_polling(self, call_id: str, on_event: EventHook | None, deadline: float) -> str | None:
        """Degraded path: no live feed, so just watch status until terminal."""
        while time.monotonic() < deadline:
            payload = self._request("GET", f"/calls/{call_id}").json()
            status = payload.get("status", "unknown")
            if on_event:
                on_event(CallEvent(id=0, type="status", data={"status": status}))
            if status in TERMINAL_STATUSES:
                return payload.get("outcome_type")
            time.sleep(2)
        return None

    # --- results -----------------------------------------------------------

    def result(self, call_id: str, wait_for_outcome: bool = True, timeout: float = 90) -> CallResult:
        """Fetch the settled result, waiting out the persistence lag.

        outcome_type/summary/transcript populate 20-30s AFTER status becomes
        `completed`. Reading immediately returns nulls — hence the wait.
        """
        if self.dry_run:
            return _dry_run_result(call_id)

        deadline = time.monotonic() + timeout
        payload: dict = {}
        while True:
            payload = self._request("GET", f"/calls/{call_id}").json()
            outcome = payload.get("outcome_type")
            status = payload.get("status", "unknown")
            done = status in TERMINAL_STATUSES and outcome is not None
            if done or not wait_for_outcome or time.monotonic() > deadline:
                break
            time.sleep(3)

        return CallResult(
            call_id=call_id,
            status=payload.get("status", "unknown"),
            outcome_type=payload.get("outcome_type"),
            # docs use outcome_summary, SKILL.md says summary — accept both
            summary=payload.get("outcome_summary") or payload.get("summary"),
            transcript=_format_transcript(payload.get("transcript_full")),
            raw=payload,
        )

    # --- convenience -------------------------------------------------------

    def call(
        self,
        target_phone: str,
        brief: str,
        answerer: Answerer | None = None,
        on_event: EventHook | None = None,
        language: str = "en",
    ) -> CallResult:
        """place -> follow -> result, in one blocking call."""
        call_id = self.place(target_phone, brief, language=language)
        self.follow(call_id, answerer=answerer, on_event=on_event)
        return self.result(call_id)


# --- dry run simulation -----------------------------------------------------
# Lets the whole state machine (menu -> call -> ask_user -> outcome -> buzz) be
# rehearsed without credits or a ringing phone.


@dataclass
class _DryRunCall:
    target_phone: str
    brief: str


_DRY_RUNS: dict[str, _DryRunCall] = {}

DRY_RUN_QUESTION = "Can you tell me where she is right now, and whether she's conscious?"
DRY_RUN_OUTCOME = "success_no_booking"


def _safe_answer(answerer: Answerer | None, question: str) -> str:
    """Never let a mid-call question go unanswered — silence is worse than 'I don't know'."""
    fallback = "I'm sorry, I don't have that information right now."
    if answerer is None:
        return fallback
    try:
        reply = answerer(question)
    except Exception:  # noqa: BLE001 - a crashing answerer must not stall a live call
        log.exception("answerer raised; falling back")
        return fallback
    return reply.strip() if isinstance(reply, str) and reply.strip() else fallback


def _dry_run_follow(call_id: str, answerer: Answerer | None, on_event: EventHook | None) -> str:
    script = [
        CallEvent(1, "status", {"status": "dialing"}),
        CallEvent(2, "status", {"status": "in_progress"}),
        CallEvent(3, "ask_user", {"request_id": "dry-q1", "message": DRY_RUN_QUESTION}),
        CallEvent(4, "outcome", {"outcome_type": DRY_RUN_OUTCOME}),
    ]
    for event in script:
        if on_event:
            on_event(event)
        if event.type == "ask_user":
            reply = _safe_answer(answerer, event.data["message"])
            log.info("DRY RUN mid-call Q: %s\n                    A: %s", event.data["message"], reply)
            _DRY_RUNS.setdefault(call_id, _DryRunCall("", "")).__dict__["answer"] = reply
        time.sleep(0.4)
    return DRY_RUN_OUTCOME


def _dry_run_result(call_id: str) -> CallResult:
    sim = _DRY_RUNS.get(call_id)
    answer = getattr(sim, "answer", "(no answerer)") if sim else "(unknown call)"
    return CallResult(
        call_id=call_id,
        status="completed",
        outcome_type=DRY_RUN_OUTCOME,
        summary="[DRY RUN] Simulated call. No phone was dialed.",
        transcript=(
            "[DRY RUN TRANSCRIPT]\n"
            "Agent: This call is with an AI assistant and is being recorded.\n"
            f"Agent: {(sim.brief[:160] + '...') if sim else ''}\n"
            f"Them:  {DRY_RUN_QUESTION}\n"
            f"Agent: {answer}\n"
            "Them:  Okay, I'm on my way.\n"
        ),
        raw={"dry_run": True},
    )
