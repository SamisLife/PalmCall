"""Demo persona + live device context.

Everything here is fake and safe to commit. Swap the phone numbers in .env, not
in this file.
"""

from __future__ import annotations

PERSON_NAME = "Margaret Chen"

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

# Mutated at runtime by the camera / IMU branch. The live_context_rung reads
# this at question time, which is why the caregiver can ask "where is she?"
# thirty seconds into a call and get a real answer.
LIVE_CONTEXT: dict[str, str] = {
    "scene": "She's on the floor in the kitchen, near the counter, lying on her side.",
    "movement": "The band has detected small arm movements in the last thirty seconds, so she is moving.",
    "detected": "A sharp impact consistent with a fall, followed by no walking motion.",
    "time": "The alert came in a little over a minute ago.",
}


def get_live_context() -> dict[str, str]:
    return LIVE_CONTEXT


def context_pack() -> str:
    """Flattened facts for the LLM rung."""
    lines = [f"Person: {PERSON_NAME}", "", "Known profile:"]
    lines += [f"- {k.replace('_', ' ')}: {v}" for k, v in PROFILE.items()]
    lines += ["", "Live device readings right now:"]
    lines += [f"- {k}: {v}" for k, v in LIVE_CONTEXT.items()]
    return "\n".join(lines)
