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

# What a snap alone tells us. A gesture carries no location, no posture and no
# cause — but the two facts below ARE things we know, and they're the ones a
# caregiver needs when they ask "what specifically?". Saying she asked for help
# and stopping there leaves them with nothing to act on.
#
# Keep this list honest: whatever is in it, the agent says on a real call to a
# real person.
BASE_FACTS: list[str] = [
    "She made this request deliberately, with the band on her wrist. It is not an automatic "
    "fall detection going off on its own.",
    "The band tells us she needs help. It cannot tell us what is wrong.",
]


def trigger_facts() -> list[str]:
    """Everything known AT THE TIME OF DIALING, camera included if it fired.

    Front-loaded into the brief rather than held back for the answerer: a
    caregiver should not have to interrogate an automated caller to find out
    where the person is. The answerer still covers everything past this —
    address, medications, whether she is still moving.
    """
    facts = list(BASE_FACTS)
    if scene := LIVE_CONTEXT.get("scene"):
        facts.append(scene)
    return facts

# Populated at RUNTIME, and only when a sensor actually produced something.
# Starts empty on purpose: with just a snap we genuinely do not know where she
# is, and the answerer will say so rather than invent a kitchen floor.
#
# The live_context_rung reads this at question time, not at call time — which
# is the whole point. A caption that lands ten seconds into the call is still
# available when the caregiver asks "where is she?" thirty seconds in.
LIVE_CONTEXT: dict[str, str] = {}


def get_live_context() -> dict[str, str]:
    return LIVE_CONTEXT


def set_camera_context(scene: str, movement: str | None = None) -> None:
    """Called by the camera branch once a VLM has captioned the frames."""
    LIVE_CONTEXT["scene"] = scene
    if movement:
        LIVE_CONTEXT["movement"] = movement


def simulate_camera() -> None:
    """Stand-in for the ESP32 + VLM path, for rehearsing without hardware.

    Only ever invoked behind an explicit `--camera` flag so the base demo stays
    honest about what a bare snap knows.
    """
    set_camera_context(
        scene="The camera shows her on the floor in the kitchen, near the counter, lying on her side.",
        movement="The band has picked up small arm movements in the last thirty seconds.",
    )


def context_pack() -> str:
    """Flattened facts for the LLM rung."""
    lines = [f"Person: {PERSON_NAME}", "", "Known profile:"]
    lines += [f"- {k.replace('_', ' ')}: {v}" for k, v in PROFILE.items()]
    if LIVE_CONTEXT:
        lines += ["", "Live device readings right now:"]
        lines += [f"- {k}: {v}" for k, v in LIVE_CONTEXT.items()]
    else:
        lines += ["", "No live sensor data is available for this alert."]
    return "\n".join(lines)
