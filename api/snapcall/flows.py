"""End-to-end flows: what actually happens after a gesture fires.

This is the layer the gesture detector calls. It owns escalation and the
guarantee that *something* reaches a human even when every clever layer fails.

Escalation ladder for the emergency branch:

    primary caregiver  ->  backup caregiver  ->  deterministic SMS fallback

Anything that is not a `success_*` outcome counts as "did not reach" — voicemail,
busy, technical error, and especially `failed_short_hangup` (they picked up, then
hung up on the AI disclosure). All four look identical to the person on the
floor: nobody is coming.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from . import briefs
from .callwright import CallResult, CallwrightClient, CallwrightError, EventHook

log = logging.getLogger("snapcall.flows")


@dataclass
class Contact:
    name: str
    phone: str
    relationship: str = "emergency contact"


@dataclass
class EscalationReport:
    """What happened across the whole ladder — this is what the dashboard shows."""

    attempts: list[CallResult] = field(default_factory=list)
    reached_via: Contact | None = None
    sms_sent_to: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def reached(self) -> bool:
        return self.reached_via is not None


def emergency(
    client: CallwrightClient,
    contacts: list[Contact],
    *,
    person_name: str,
    detected: str,
    location: str,
    callback_number: str,
    answerer: Callable[[str], str] | None = None,
    on_event: EventHook | None = None,
    sms_fallback: Callable[[str, str], None] | None = None,
    extra_context: str = "",
) -> EscalationReport:
    """Work down the contact list until a human actually answers.

    Returns as soon as one contact is genuinely reached. If nobody is, fires the
    SMS fallback so the alert lands even with the whole AI layer dead.
    """
    report = EscalationReport()

    for contact in contacts:
        if not contact.phone:
            continue
        log.info("escalation: calling %s (%s)", contact.name, contact.relationship)
        brief = briefs.emergency_brief(
            person_name=person_name,
            caregiver_name=contact.name,
            relationship=contact.relationship,
            detected=detected,
            location=location,
            callback_number=callback_number,
            extra_context=extra_context,
        )
        try:
            result = client.call(contact.phone, brief, answerer=answerer, on_event=on_event)
        except CallwrightError as exc:
            log.error("call to %s failed outright: %s", contact.name, exc)
            report.error = str(exc)
            continue

        report.attempts.append(result)
        if result.reached:
            log.info("reached %s (%s)", contact.name, result.outcome_type)
            report.reached_via = contact
            return report
        log.warning("did not reach %s (%s) — escalating", contact.name, result.outcome_type)

    if sms_fallback:
        text = (
            f"EMERGENCY: {person_name} triggered her wristband. Detected: {detected}. "
            f"Location: {location}. Nobody answered the alert calls. Callback {callback_number}."
        )
        for contact in contacts:
            if not contact.phone:
                continue
            try:
                sms_fallback(contact.phone, text)
                report.sms_sent_to.append(contact.phone)
            except Exception as exc:  # noqa: BLE001 - last resort, log and keep going
                log.error("SMS fallback to %s failed: %s", contact.phone, exc)

    return report


def errand(
    client: CallwrightClient,
    *,
    business_name: str,
    business_phone: str,
    person_name: str,
    task: str,
    callback_number: str,
    known_details: dict[str, str] | None = None,
    answerer: Callable[[str], str] | None = None,
    on_event: EventHook | None = None,
) -> CallResult:
    """Run one errand call. No escalation — a pharmacy that doesn't answer gets retried, not replaced."""
    brief = briefs.errand_brief(
        business_name=business_name,
        person_name=person_name,
        task=task,
        callback_number=callback_number,
        known_details=known_details,
    )
    return client.call(business_phone, brief, answerer=answerer, on_event=on_event)
