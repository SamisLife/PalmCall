"""Telling the wearer what happened.

The band has no microphone, and it does not need one — a gesture replaces
speech on the way IN. But the wearer still has to learn whether anyone is
actually coming, and that is a problem of OUTPUT, not input.

Two channels, deliberately different bandwidths:

* **Haptics** (`TriggerHub.buzz`) — one long buzz reached, two short did not.
  Works anywhere, needs no attention, survives a dead network.
* **Speech** (here) — the actual answer, out loud: "Sarah has been reached.
  She can get to you immediately." Nonverbal does not mean deaf; most people
  who cannot operate a phone can hear one perfectly well.

Speech is the richer channel and it needs no hardware we don't already have.
Haptics remain the fallback for when the wearer is away from the speaker.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess

log = logging.getLogger("snapcall.notify")


def _say_available() -> bool:
    return platform.system() == "Darwin" and shutil.which("say") is not None


def speak(text: str, voice: str = "Samantha") -> None:
    """Say `text` out loud. Never raises — this is a courtesy channel.

    Non-blocking so it cannot stall the call state machine.
    """
    if not text:
        return
    log.info("SPEAK: %s", text)
    if not _say_available():
        return  # other platforms: the log line is the record
    try:
        subprocess.Popen(  # noqa: S603 - fixed binary, no shell
            ["say", "-v", voice, text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001 - never let a nicety break the flow
        log.debug("say failed", exc_info=True)


def first_sentence(text: str | None, limit: int = 180) -> str:
    """Trim a Callwright summary down to something worth hearing aloud.

    The summaries are written for a dashboard, not an ear — several clauses
    long. The wearer needs the headline, not the audit trail.
    """
    if not text:
        return ""
    head = text.strip().split(". ")[0].strip().rstrip(".")
    return head[:limit] + ("…" if len(head) > limit else "")


def announce_outcome(reached_name: str | None, summary: str | None) -> str:
    """Compose the spoken confirmation for the wearer."""
    if reached_name:
        detail = first_sentence(summary)
        line = f"{reached_name} has been reached."
        return f"{line} {detail}." if detail else line
    return "I could not reach anyone. Trying another way."
