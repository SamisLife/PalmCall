"""Mid-call answerers.

When the voice agent hits something it wasn't told, it emits an `ask_user` event
and waits ~60 seconds for a reply. Whoever is on the phone is sitting in silence
for that whole window, so:

    THE ANSWERER MUST ALWAYS RETURN SOMETHING.

A truthful "I don't have that information" is far better than a timeout. Every
rung of the chain below terminates in that decline rather than raising.

The chain is what makes the system degrade gracefully instead of failing:

    live context (camera / sensors)  ->  profile lookup  ->  LLM  ->  decline

Drop any rung and the call still completes; you just get a thinner conversation.
"""

from __future__ import annotations

import logging
from typing import Callable, Protocol

log = logging.getLogger("snapcall.answerer")

DECLINE = "I'm sorry, I don't have that information right now."


class Rung(Protocol):
    """One source of answers. Returns None to pass the question down the chain."""

    def __call__(self, question: str) -> str | None: ...


def chain(*rungs: Rung, decline: str = DECLINE) -> Callable[[str], str]:
    """Compose rungs into an answerer that always returns a string."""

    def answer(question: str) -> str:
        for rung in rungs:
            try:
                reply = rung(question)
            except Exception:  # noqa: BLE001 - a broken rung must not stall a live call
                log.exception("answerer rung %r failed; continuing down the chain", rung)
                continue
            if reply and reply.strip():
                return reply.strip()
        log.info("no rung could answer %r — declining", question)
        return decline

    return answer


# --- rung: static profile ---------------------------------------------------


def profile_rung(profile: dict[str, str], aliases: dict[str, list[str]] | None = None) -> Rung:
    """Keyword-match the question against known facts.

    Deliberately dumb and dependency-free so it works with no network. This is
    the rung that answers "what's her date of birth?" on an errand call.
    """
    aliases = aliases or DEFAULT_ALIASES

    def rung(question: str) -> str | None:
        lowered = question.lower()
        for key, value in profile.items():
            terms = [key.replace("_", " "), *aliases.get(key, [])]
            if any(term in lowered for term in terms):
                return f"{key.replace('_', ' ').capitalize()}: {value}"
        return None

    return rung


DEFAULT_ALIASES: dict[str, list[str]] = {
    "date_of_birth": ["date of birth", "birthday", "born", "dob", "how old"],
    "full_name": ["full name", "last name", "spell", "her name"],
    "address": ["address", "where does she live", "street", "zip"],
    "phone": ["phone number", "callback", "reach you", "contact number"],
    "member_id": ["member id", "member number", "insurance", "policy", "group number"],
    "prescription_number": ["prescription", "rx number", "refill number"],
    "pharmacy": ["pharmacy", "which store", "location"],
    "allergies": ["allerg"],
    "medications": ["medication", "what does she take", "prescribed"],
    "doctor": ["doctor", "physician", "prescriber", "who prescribed"],
}


# --- rung: live device context ----------------------------------------------


def live_context_rung(get_context: Callable[[], dict[str, str]]) -> Rung:
    """Answer from whatever the device knows right now.

    `get_context` is called at question time, not at call time — that's the
    point. The caregiver asks "where is she?" thirty seconds into the call and
    we answer with the caption the camera produced after the alert fired.
    """
    triggers = {
        "scene": ["where", "what do you see", "camera", "what happened", "which room", "what's going on", "position"],
        "movement": ["moving", "conscious", "responsive", "awake", "breathing", "still", "alright", "ok"],
        "detected": ["what triggered", "how do you know", "what did it detect", "fall", "why"],
        "time": ["when", "how long", "what time"],
    }

    def rung(question: str) -> str | None:
        lowered = question.lower()
        context = get_context() or {}
        # Answer EVERY part that matched, not just the first. Caregivers ask
        # compound questions ("where is she, and is she conscious?") and a
        # half-answer forces them to ask again on a call that is already tense.
        matched = [
            context[key]
            for key, terms in triggers.items()
            if key in context and any(term in lowered for term in terms)
        ]
        return " ".join(matched) if matched else None

    return rung


# --- rung: LLM over a context pack ------------------------------------------


def llm_rung(context_pack: str, model: str = "claude-fable-5") -> Rung:
    """Catch-all for questions the other rungs didn't anticipate.

    Constrained hard: answer ONLY from the pack, one or two spoken sentences,
    and return the empty string rather than guessing. Requires ANTHROPIC_API_KEY;
    if the SDK or key is missing the rung disables itself and the chain falls
    through to the decline.
    """
    try:
        import anthropic  # noqa: PLC0415 - optional dependency, checked at build time
    except ImportError:
        log.info("anthropic SDK not installed — llm_rung disabled")
        return lambda question: None

    try:
        client = anthropic.Anthropic()
    except Exception:  # noqa: BLE001 - missing key etc.
        log.info("anthropic client unavailable — llm_rung disabled")
        return lambda question: None

    system = (
        "You are supplying answers to a voice agent that is MID-CALL on a real phone line. "
        "Someone just asked the question below and is waiting in silence.\n\n"
        "Rules:\n"
        "- Answer ONLY from the context provided. Never invent a name, number, date, or medical detail.\n"
        "- If the context does not contain the answer, reply with exactly: UNKNOWN\n"
        "- Otherwise reply with one or two short sentences, written to be spoken aloud.\n"
        "- Never diagnose or assess medical severity.\n\n"
        f"CONTEXT:\n{context_pack}"
    )

    def rung(question: str) -> str | None:
        response = client.messages.create(
            model=model,
            max_tokens=150,
            system=system,
            messages=[{"role": "user", "content": question}],
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text or text.upper().startswith("UNKNOWN"):
            return None
        return text

    return rung
