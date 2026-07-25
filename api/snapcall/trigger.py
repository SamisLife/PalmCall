"""The seam between gesture detection and placing a call.

Whatever detects the snap — the band over BLE, a laptop mic, a keypress, curl —
calls `TriggerHub.snap()`. Nothing upstream of this needs to know Callwright
exists, and nothing downstream needs to know what kind of sensor fired.

Two behaviours live here because they are policy, not detection:

* **Cancel window.** A snap does not dial immediately. It arms, buzzes, and
  waits a few seconds; a second snap inside that window cancels. This is what
  lets the detector be tuned aggressively — a false positive costs a vibration
  instead of a phone call to a caregiver.

* **Single flight.** While a call is in progress, further snaps are ignored.
  Without this a nervous user double-triggers and we place two emergency calls
  to the same person, or hit the 3-call concurrency cap mid-demo.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import demo_data, flows, notify
from .callwright import CallEvent, CallwrightClient

log = logging.getLogger("snapcall.trigger")

# idle -> armed -> calling -> done|cancelled -> idle
State = str


@dataclass
class HubStatus:
    state: State = "idle"
    detail: str = ""
    source: str = ""
    transcript: list[str] = field(default_factory=list)
    outcome: str | None = None


class TriggerHub:
    """Owns the state machine from 'snap detected' to 'result known'."""

    def __init__(
        self,
        client: CallwrightClient,
        contacts: list[flows.Contact],
        *,
        answerer: Callable[[str], str] | None = None,
        cancel_seconds: float = 3.0,
        on_state: Callable[[HubStatus], None] | None = None,
        buzz: Callable[[str], None] | None = None,
        speak: Callable[[str], None] | None = None,
    ):
        self.client = client
        self.contacts = contacts
        self.answerer = answerer
        self.cancel_seconds = cancel_seconds
        self.on_state = on_state
        # buzz("confirm"|"cancelled"|"success"|"failure") -> drive the wristband.
        # Left as a no-op until the BLE side lands; the state machine does not
        # care whether anything is actually vibrating.
        self.buzz = buzz or (lambda pattern: log.info("BUZZ: %s", pattern))
        self.speak = speak or notify.speak

        self.status = HubStatus()
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._busy = False

    # --- public API ---------------------------------------------------------

    def snap(self, source: str = "unknown") -> str:
        """Called by whatever detected the gesture. Returns what it did.

        Non-blocking: the call runs on a worker thread so an HTTP handler or a
        BLE callback returns immediately.
        """
        with self._lock:
            if self.status.state == "armed":
                # Second snap inside the window — this is a cancel.
                self._cancel.set()
                return "cancelled"
            if self._busy:
                log.info("snap from %s ignored — call already in progress", source)
                return "ignored_busy"
            self._busy = True
            self._cancel.clear()

        self._set_state("armed", f"snap from {source} — {self.cancel_seconds:.0f}s to cancel", source)
        self.buzz("confirm")
        threading.Thread(target=self._run, args=(source,), daemon=True).start()
        return "armed"

    def set_context(self, scene: str, movement: str | None = None) -> None:
        """Camera branch reporting in. Safe to call mid-call — the answerer
        reads LIVE_CONTEXT at question time, not at dial time."""
        demo_data.set_camera_context(scene, movement)
        log.info("live context updated: %s", scene)

    # --- internals ----------------------------------------------------------

    def _set_state(self, state: State, detail: str = "", source: str = "") -> None:
        self.status.state = state
        self.status.detail = detail
        if source:
            self.status.source = source
        log.info("[%s] %s", state.upper(), detail)
        if self.on_state:
            self.on_state(self.status)

    def _on_event(self, event: CallEvent) -> None:
        if event.type == "transcript":
            line = f"{event.data.get('speaker', '?')}: {event.data.get('text', '')}"
            self.status.transcript.append(line)
            if self.on_state:
                self.on_state(self.status)
        elif event.type == "status":
            self._set_state("calling", str(event.data.get("status", "")))

    def _run(self, source: str) -> None:
        try:
            if self._cancel.wait(self.cancel_seconds):
                self._set_state("cancelled", "second snap — call aborted")
                self.buzz("cancelled")
                time.sleep(1)
                self._set_state("idle")
                return

            self.status.transcript.clear()
            self.status.outcome = None
            self._set_state("calling", "placing the call")

            report = flows.emergency(
                self.client,
                self.contacts,
                person_name=demo_data.PERSON_NAME,
                callback_number=demo_data.PROFILE["phone"],
                known_facts=demo_data.trigger_facts(),
                answerer=self.answerer,
                on_event=self._on_event,
            )

            last = report.attempts[-1] if report.attempts else None
            self.status.outcome = last.outcome_type if last else "no_attempt"
            if last and last.transcript:
                self.status.transcript = last.transcript.splitlines()

            # Closing the loop is what makes this a communication device rather
            # than a panic button. Buzz says whether it worked; speech says what
            # was actually agreed, which is the part the wearer cares about.
            reached_name = report.reached_via.name if report.reached_via else None
            self.speak(notify.announce_outcome(reached_name, last.summary if last else None))

            if report.reached:
                self.buzz("success")
                self._set_state("done", f"reached {reached_name}")
            else:
                self.buzz("failure")
                self._set_state("done", f"nobody reached ({self.status.outcome})")
        except Exception as exc:  # noqa: BLE001 - a worker thread must never die silently
            log.exception("trigger run failed")
            self.buzz("failure")
            self._set_state("error", str(exc))
        finally:
            with self._lock:
                self._busy = False
