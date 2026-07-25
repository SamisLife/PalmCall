"""Demo persona and what the device actually knows.

Everything here is fake and safe to commit. Swap the phone numbers in .env, not
in this file.

**The camera is a gesture detector, not an observer.** It is low resolution and
its entire job is to recognise a hand signal — a snap. It cannot see where
someone is, what position they are in, whether they are hurt, or what happened.
There is deliberately no scene description anywhere in this file, because the
hardware cannot produce one and the agent must never say it on a real call.
"""

from __future__ import annotations

PERSON_NAME = "Margaret Chen"

# Static, on-file facts. Legitimately known because someone entered them during
# setup — unlike anything about the current moment, which we cannot see.
PROFILE: dict[str, str] = {
    "full_name": "Margaret Chen, C-H-E-N",
    "date_of_birth": "March 12th, 1948",
    "address": "418 Bayview Terrace, Apartment 2B, San Francisco",
    "phone": "415-555-0199",
    "member_id": "Blue Shield, member ID X-J-4-1-9-8-8-2-3",
    "prescription_number": "RX 7741903",
    "pharmacy": "the Walgreens on 16th and Mission",
    "medications": "Lisinopril 10 milligrams, once daily",
    "allergies": "penicillin",
    "doctor": "Dr. Amara Okafor",
}

# What a detected snap tells us, and the honest edges of it. Both lines answer
# the question a caregiver actually asks — "what specifically?" — without
# claiming anything the detector cannot support.
#
# Whatever is in this list, the agent says on a real call to a real person.
BASE_FACTS: list[str] = [
    "She made the signal deliberately. It is a hand gesture she has to perform on purpose, "
    "not an alarm going off on its own.",
    "That signal is all the system can detect. It cannot tell us what is wrong, where she is, "
    "or whether she is hurt.",
]


def trigger_facts() -> list[str]:
    """Everything known at the time of dialing. Front-loaded into the brief."""
    return list(BASE_FACTS)


def context_pack() -> str:
    """Flattened facts for the LLM rung — on-file data only."""
    lines = [f"Person: {PERSON_NAME}", "", "Known profile:"]
    lines += [f"- {k.replace('_', ' ')}: {v}" for k, v in PROFILE.items()]
    lines += [
        "",
        "The alert came from a snap gesture detected by a low-resolution camera. There is NO "
        "information about her current location, position, or condition. If asked about any of "
        "that, the answer is that we do not know.",
    ]
    return "\n".join(lines)
