---
name: snapcall
description: Use when working in the SnapCall repo — placing test calls, writing or editing a Callwright brief, adding an answerer rung for mid-call questions, wiring the gesture detector to a call, or debugging a call that failed. Covers the project's own call layer, not the raw API (see the callwright skill for that).
version: 0.1.0
---

# SnapCall — working in this repo

Wearable gesture → real phone call. `api/snapcall/callwright.py` wraps the
Callwright API; everything else composes on top of it.

**All Python lives in `api/`** — its own uv project, `.env`, and virtualenv. Run
commands from there (or `uv run --directory api ...` from the repo root).

**Before doing anything that dials: dry run is the default and must stay that
way.** `--live` is the only switch that rings a phone.

## Place a test call

```sh
cd api
uv run python -m snapcall.cli preflight       # ALWAYS run this first
uv run python -m snapcall.cli emergency       # simulated, incl. a mid-call question
uv run python -m snapcall.cli errand
uv run python -m snapcall.cli raw --to +14155550199 --brief "..."
```

Dry run simulates the whole event stream — dialing, in-progress, an `ask_user`
question, and an outcome — so the state machine is fully rehearsable without
credits or a ringing phone. Use it to test any change to briefs, answerers, or
escalation.

Going live: `--live`, and the number must be in `CALLWRIGHT_ALLOWED_NUMBERS`.

## Writing a brief

The `brief` is the **entire program** the voice agent runs. There is no separate
caller-name, script, or metadata field. Anything not in the string does not
exist to the agent — it will either omit the fact or invent one.

Rules, in priority order:

1. **Front-load the reason.** `failed_short_hangup` is the most common failure:
   someone picks up, hears the mandatory AI disclosure, hangs up. The first
   sentence must carry who this is about and why you're calling. Never open with
   pleasantries or self-introduction.
2. **Pre-answer the predictable questions.** A pharmacy *will* ask for a date of
   birth. Put known verification data directly in the brief (`known_details` in
   `errand_brief`) so the call survives even if the live answerer is down.
3. **Forbid guessing explicitly.** Tell the agent to ask the operator system
   rather than invent a name, number, date, or medical detail.
4. **Say how to end.** Read back what was agreed, get explicit confirmation,
   give the callback number.
5. **No medical claims on the emergency branch.** The agent must not diagnose or
   assess severity, and must redirect to 911.

Add new call types as a builder in `briefs.py`. Do not fork the call layer.

## Mid-call questions (`ask_user`)

The agent can ask *our system* a question mid-call and waits ~60s for a reply.
This is the project's best feature — the caregiver asks "where is she?" and we
answer from the camera caption, live, on the call.

`answerer.py` composes rungs into a chain:

```python
answerer = chain(
    live_context_rung(demo_data.get_live_context),  # camera / sensors, read at question time
    profile_rung(demo_data.PROFILE),                 # static facts: DOB, insurance, Rx
    llm_rung(demo_data.context_pack()),              # catch-all, constrained to the pack
)
```

A rung returns `None` to pass the question down; the chain terminates in a
truthful decline. **Never let a rung return empty or raise into the call** —
silence for 60 seconds on a live emergency call is much worse than "I don't have
that information."

Adding a rung: write `(question: str) -> str | None`, insert it in the chain in
`cli._build_answerer`. Order matters — most specific first.

`live_context_rung` answers *every* part of a compound question, not just the
first match, because caregivers ask "where is she, and is she conscious?"

## Debugging a failed call

1. Read `transcript_full`, not just the outcome code. `success_no_booking` is a
   *billable success* — information was obtained.
2. `failed_short_hangup` → the brief's opening was too slow. Front-load harder.
3. Agent said something wrong or vague → the fact was missing from the brief, or
   no rung could answer it. Check the logged `mid-call Q:` / `A:` pair.
4. Nulls in the result → persistence lag; `result()` waits it out, `follow()`
   deliberately does not.
5. `402` with credits remaining → the 200-credit reservation floor.
6. Warning about "no live event feed" → the events endpoint isn't available and
   `ask_user` is silently lost. Escalate this; it breaks the best demo moment.

## Escalation

`flows.emergency()` walks the contact list until `CallResult.reached` is true,
then fires an SMS fallback if nobody was reached. All six `failed_*` outcomes
count as not reached. Keep it that way — voicemail and a hangup are identical to
the person who needs help.
