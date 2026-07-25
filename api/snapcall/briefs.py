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
    callback_number: str,
    known_facts: list[str] | None = None,
    now: datetime | None = None,
) -> str:
    """Alert a designated caregiver that the wearer asked for help.

    `known_facts` is ONLY what the system actually observed. Pass none and the
    agent says nothing beyond "she asked for help" — which is the honest base
    case, because a snap gesture tells us a request was made and nothing else.
    Position, posture and movement belong here only when a camera or sensor
    genuinely produced them.

    This is the line between a demo and a lie: the agent must never narrate
    detail the hardware did not generate. Anything the caregiver asks beyond
    these facts goes to the live answerer mid-call, so richer context can still
    arrive during the call without being fabricated up front.
    """
    facts = [f"She triggered the alert at {_clock(now)}.", *(known_facts or [])]

    # Written as one literal script rather than a numbered plan. Given steps,
    # the agent delivers step one and yields the turn — so the caregiver hears
    # "she needs immediate assistance" followed by silence, and has to drag the
    # rest out of it. Handing it a single continuous line that ENDS on the
    # question keeps the turn and makes the ask unmissable.
    script = " ".join(
        [
            f"This is an urgent alert about {person_name}. She has requested immediate assistance.",
            *facts,
            "Can you get to her now, and how long will it take?",
        ]
    )

    return "\n".join(
        [
            f"URGENT. You are calling {caregiver_name}, the {relationship} of {person_name} and her "
            f"emergency contact.",
            "",
            "Your FIRST turn must be all of the following, delivered in one go. Do not pause for a "
            "response, do not wait to be asked, and do not hand the conversation back until you "
            "have asked the question at the end:",
            "",
            f'"{script}"',
            "",
            "People hang up on unknown numbers, so this has to land in the first few seconds. If "
            "they interrupt you partway, answer them briefly and then finish the rest — but always "
            "end your turn on the question. Never leave them with a statement and silence.",
            "",
            "If they ask what happened, what is wrong, where she is, or whether she is hurt: we do "
            "not know, and you must say so plainly. The system detects the signal and nothing "
            "else. Add that this is exactly why someone needs to go to her. Never speculate about "
            "what might have happened, and never downplay it to make them feel better.",
            "",
            "If they ask for something on file — her address, her medications, her doctor, her "
            "date of birth — ask the operator system and relay exactly what it tells you. If it "
            "has nothing, say we don't know rather than guessing.",
            "",
            "You are not a medical professional. Do not assess how serious this is. If they cannot "
            "go, tell them to call 911.",
            "",
            f"Before ending: confirm what they said they will do, give the callback number "
            f"{callback_number}, then end the call.",
        ]
    )


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
