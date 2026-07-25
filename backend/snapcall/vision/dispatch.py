"""Bridge from a recognised gesture to a placed phone call.

The detector runs a camera loop at frame rate. `CallwrightClient.call()` blocks
for the entire duration of a real phone call — 40 to 90 seconds. Calling it
inline would freeze the video window for the whole call, which is both a broken
demo and a broken product.

So the detector never calls the API directly. It calls `dispatch(event)`, which
hands off to a `TriggerHub` that already owns:

* a worker thread, so the camera loop returns immediately
* the 3-second cancel window (a second gesture aborts the call)
* single-flight, so a re-detected gesture cannot place a second call

That last one matters more than it looks: the detector's own cooldown stops a
*held* palm from re-firing, but it does not stop a second person, a reflection,
or a recovered stream from triggering while a call is already live.

The dashboard is served from a background thread here too, so one command gives
you the camera window, the live call state, and the transcript.
"""

from __future__ import annotations

import logging
import threading

from .. import config, flows
from ..answerer import chain, profile_rung
from ..callwright import CallwrightClient
from ..demo_data import PROFILE
from ..trigger import TriggerHub

log = logging.getLogger("snapcall.vision.dispatch")

_hub: TriggerHub | None = None
_lock = threading.Lock()


def get_hub(*, live: bool = False, serve_port: int | None = 8787) -> TriggerHub:
    """Build (once) the hub every gesture feeds into."""
    global _hub
    with _lock:
        if _hub is not None:
            return _hub

        contacts = [
            flows.Contact("Sarah", config.CAREGIVER_PRIMARY_PHONE, "daughter"),
            flows.Contact("David", config.CAREGIVER_BACKUP_PHONE, "neighbor"),
        ]
        contacts = [c for c in contacts if c.phone]
        if not contacts:
            raise RuntimeError(
                "No caregiver numbers configured. Set CAREGIVER_PRIMARY_PHONE in .env."
            )

        _hub = TriggerHub(
            CallwrightClient(dry_run=not live),
            contacts,
            answerer=chain(profile_rung(PROFILE)),
        )

        if serve_port:
            from ..server import serve

            threading.Thread(
                target=serve, args=(_hub,), kwargs={"port": serve_port}, daemon=True
            ).start()
            log.info("dashboard on http://localhost:%d", serve_port)

        log.info(
            "dispatch ready — %s mode, contacts: %s",
            "LIVE" if live else "dry run",
            ", ".join(c.name for c in contacts),
        )
        return _hub


def dispatch(event: dict, *, live: bool = False) -> str:
    """Called by the detector the instant a qualifying gesture is recognised.

    Returns what the hub did: "armed", "cancelled", or "ignored_busy".
    Never raises — a failure here must not take down the camera loop.
    """
    try:
        hub = get_hub(live=live)
        action = hub.snap(source=f"vision:{event.get('gesture', 'gesture')}")
        log.info("gesture dispatched -> %s", action)
        return action
    except Exception:  # noqa: BLE001 - the camera loop must survive anything
        log.exception("dispatch failed")
        return "error"
