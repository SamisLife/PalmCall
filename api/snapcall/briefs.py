"""Brief builders.

The `brief` is the entire program the voice agent runs. There is no separate
caller-name, script, or metadata field — if a fact is not in this string, the
agent does not have it and will either omit it or invent it.

Two rules learned from the outcome taxonomy:

1. `failed_short_hangup` is the most common failure — someone picks up, hears
   the automatic AI disclosure, and hangs up. So the FIRST sentence the agent
   speaks must carry the name and the reason. Never open with pleasantries.

2. Anything the other side might ask must either be pre-answered in the brief or
   answerable by the live answerer (see answerer.py). A pharmacy WILL ask for a
   date of birth; if nothing can supply it, the call stalls.
"""

from __future__ import annotations

from datetime import datetime


def _clock(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%-I:%M %p")


def emergency_brief(
    *,
    person_name: str,
    caregiver_name: str,
    relationship: str,
    detected: str,
    location: str,
    callback_number: str,
    extra_context: str = "",
    now: datetime | None = None,
) -> str:
    """Alert a designated caregiver that the wearer triggered an emergency.

    `detected` is what the system actually observed ("a fall-pattern impact
    followed by no movement for 40 seconds"), not an inference. Keep the agent
    honest — it should never claim a medical diagnosis.
    """
    lines = [
        f"URGENT PERSONAL SAFETY CALL. You are calling {caregiver_name}, who is the {relationship} "
        f"of {person_name} and is her designated emergency contact.",
        "",
        f"Open with EXACTLY this, immediately, before anything else: "
        f"\"This is an urgent automated alert about {person_name}. She has triggered her emergency "
        f"wristband and may need help right now.\" Say that first even if the person sounds confused "
        f"or tries to interrupt — people hang up on unknown numbers, so the reason for the call must "
        f"land in the first few seconds.",
        "",
        "Then give these facts, plainly and calmly:",
        f"- The alert came in at {_clock(now)}.",
        f"- What the device detected: {detected}.",
        f"- Where she is: {location}.",
        f"- {person_name} cannot use a phone herself. This alert was raised by a wearable device on "
        f"her wrist, not by her speaking.",
    ]
    if extra_context:
        lines.append(f"- Additional context: {extra_context}")

    lines += [
        "",
        "Then ask clearly: can you get to her now, and roughly how long will it take?",
        "",
        f"If {caregiver_name} asks anything you were not told — her exact position, whether she is "
        f"conscious or moving, what the camera sees, her medications — DO NOT GUESS and DO NOT "
        f"reassure them with invented detail. Use your ability to ask the operator system for the "
        f"answer, then relay exactly what you are told. If the system says it does not know, say "
        f"plainly that the information is not available.",
        "",
        f"IMPORTANT LIMITS: You are not a medical professional and must not diagnose, assess "
        f"severity, or tell anyone this is or is not serious. If {caregiver_name} cannot go, or "
        f"sounds unsure, tell them to call 911 directly — this device is not an emergency service "
        f"and does not contact one.",
        "",
        f"Before ending, confirm out loud what they said they will do, give the callback number "
        f"{callback_number}, and repeat it a second time slowly. Then thank them and end the call.",
    ]
    return "\n".join(lines)


def errand_brief(
    *,
    business_name: str,
    person_name: str,
    task: str,
    callback_number: str,
    known_details: dict[str, str] | None = None,
    wrap_up: str = "",
) -> str:
    """Run an everyday errand that is locked behind a phone call.

    `known_details` is pre-loaded verification data (date of birth, member ID,
    prescription number). Front desks and pharmacies ask for these constantly;
    putting them in the brief avoids a stall even if the live answerer is down.
    """
    lines = [
        f"You are calling {business_name} on behalf of {person_name}, who is unable to use a phone "
        f"herself. You are her authorized assistant and she has asked you to make this call.",
        "",
        f"Open with: \"Hi, I'm calling on behalf of {person_name}.\" Then state the request plainly.",
        "",
        f"THE TASK: {task}",
    ]

    if known_details:
        lines += ["", "If they ask to verify the account, you already have these details — give them:"]
        lines += [f"- {label}: {value}" for label, value in known_details.items()]

    lines += [
        "",
        "If you reach an automated phone menu, navigate it to reach a human or the correct "
        "department. If you are put on hold, wait.",
        "",
        f"If they ask for something you were not given, ask the operator system for it rather than "
        f"guessing. If it is unavailable, say so honestly and ask what the alternative is — do not "
        f"invent a date of birth, an ID number, or an address.",
        "",
        f"Callback number if they need one: {callback_number}.",
    ]

    lines += [
        "",
        wrap_up
        or "Before ending, read back exactly what was agreed — what will happen, when, and anything "
        "she needs to bring or do. Get explicit confirmation, thank them, and end the call.",
    ]
    return "\n".join(lines)
