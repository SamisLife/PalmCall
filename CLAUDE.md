# SnapCall

Wearable gesture → real phone call. A wristband gesture (snap) triggers the
laptop to place an actual phone call on the wearer's behalf via the **Callwright
API** (VOYGR, YC W26), then buzzes the result back to the wrist.

The point: give a phone to people who can't use one — older adults, nonverbal
people, people who can't work a smartphone. *She didn't touch a screen, didn't
say a word, and got what she needed.*

## Two branches, one code path

- **Errand** — refill a prescription, book an appointment, ask a front desk.
  This is what Callwright is actually built for (reaching businesses that have a
  phone number but no API) and it's the strongest demo.
- **Emergency** — call a caregiver, escalate to a backup, fall back to SMS.

Both are the same `POST /calls` with a different `brief` string. Never fork the
call layer to add a branch — add a brief builder.

## Layout

One Python project at the repo root. Vision and the call layer are the same
program — the detector calls straight into the call flow — so they share a
`pyproject.toml`, a `.env`, and a virtualenv.

```
CLAUDE.md            this file — repo-wide
pyproject.toml       uv project root — run everything from here
.env                 gitignored; the Callwright key lives here
.claude/skills/
  callwright/        vendored upstream skill (the raw API reference)
  snapcall/          how to work in THIS repo
snapcall/
  config.py          env loading + the dial allow-list (safety)
  callwright.py      API client: place / follow / answer / result, outcomes
  briefs.py          brief builders — emergency_brief, errand_brief
  answerer.py        mid-call ask_user answering, as a fallback chain
  flows.py           escalation ladder + end-to-end flows
  trigger.py         the seam: gesture in -> cancel window -> call out
  server.py          POST /trigger endpoint + live dashboard
  demo_data.py       persona + what the device actually knows
  cli.py             entry points
  vision/            laptop-side camera pipeline (opt-in extra)
    detector.py      MediaPipe gesture recognition + alert state machine
    stream_viewer.py MJPEG reader for the ESP32 /stream endpoint
    dispatch.py      gesture -> TriggerHub (threaded, never blocks the camera)
    models/          gesture_recognizer.task
firmware/            ESP32 (Seeed XIAO ESP32S3 Sense) — Arduino sketch
  snapcall_cam/      camera capture + MJPEG /stream server
    secrets.h        gitignored; WiFi creds. Copy secrets.example.h.
```

Vision deps (mediapipe, opencv) are an **optional extra** — they're large, and
the call layer, dashboard and every dry run work without them:

```sh
uv sync                  # call layer only
uv sync --extra vision   # + camera pipeline
```

**The camera is a gesture detector, not an observer.** It is low resolution and
its only job is recognising a hand signal — a snap. It cannot report location,
posture, or condition, so nothing in this codebase claims otherwise. A question
like "where is she?" correctly falls through to "I don't have that information",
because we genuinely do not.

The firmware's entire contract with the API side is one HTTP call:

```
POST http://<laptop-ip>:8787/trigger   {"source": "band"}
```

It never needs to know Callwright exists.

## Commands

All from the repo root.

```sh
# the full demo: camera -> gesture -> call, with dashboard on :8787
uv run python -m snapcall.vision.detector --address <esp32-ip>

# no camera: HTTP endpoint + dashboard, fire with curl or the ESP32
uv run python -m snapcall.cli serve

# no camera, no network: ENTER is the gesture
uv run python -m snapcall.cli listen

# checks and one-shots
uv run python -m snapcall.cli preflight     # key alive? credits? connectivity?
uv run python -m snapcall.cli emergency
uv run python -m snapcall.cli raw --to +1... --brief "..."
```

**Everything is a simulation unless you pass `--live`.** Dry run walks the whole
state machine — including a mid-call question — without dialing or spending
credits. `--llm` adds the LLM answerer rung.

## Safety rules — do not relax these

1. **`.env` is gitignored and the key never gets committed, logged, or echoed.**
   Use `config.redact()` if a key must appear in output.
2. **Dry run is the default.** `CALLWRIGHT_DRY_RUN=1` unless deliberately
   overridden. `--live` is the only way to ring a phone from the CLI.
3. **`CALLWRIGHT_ALLOWED_NUMBERS` is an allow-list enforced on every dial**
   (`config.assert_dialable`). Keep team cell numbers in it. A typo'd number is
   a real call to a real stranger.
4. **The answerer never returns empty.** A mid-call question left unanswered
   means ~60 seconds of dead silence on a live call. Always degrade to
   `"I don't have that information right now."`
5. **No medical claims.** The briefs explicitly forbid the agent from
   diagnosing or assessing severity, and tell it to redirect to 911. This is a
   prototype, not a certified medical alert device.

## Callwright API — what actually matters

Base `https://api.voygr.tech`, header `X-API-Key`. One endpoint does the work:
`POST /calls` with `target_phone`, `brief`, `language`, `ask_user_mode`.

**The `brief` is the entire program.** There is no caller-name field, no script
field. Anything not in that string, the agent doesn't know.

### Verified against the live API (2026-07-24, key "Hackathon 3")

- `quota_limit` 2000, `max_concurrent_calls` **3** (docs said ~2).
- `ask_user_webhook_configured: false` — there is a **webhook** route for
  mid-call questions in addition to the event feed. If the SSE path proves
  unreliable, ask VOYGR to point `ask_user_webhook_url` at an ngrok tunnel.
- **`transcript_full` is a list of `{"role": "bot"|"operator", "text": ...}`
  turns**, not the string the docs imply. `_format_transcript()` normalises it.
- **The agent speaks its own opener first** — `"Hi there — can you hear me
  okay?"` — before anything from our brief. We do not control the first
  utterance, so brief front-loading lands on the agent's *second* turn.
- `failed_short_hangup` really does bill 0 credits. Rehearsing failures is free.

### Known documentation contradictions (client handles both)

- **Response envelope.** Public docs say freeform returns
  `{"call": {...}, "task_id", ...}` with no top-level `call_id`; `SKILL.md` says
  grab a top-level `call_id`. → `_extract_call_id()` accepts either shape.
- **Events endpoint.** `SKILL.md` documents `GET /calls/{id}/events`, but that
  operation is absent from the published OpenAPI list. It does **not** 404 — it
  is a real SSE stream that holds the connection open. A plain blocking GET
  read-timeouts and `requests` discards the buffered body, which is why the
  first live call reported "no live event feed". `_fetch_events()` now streams
  with `iter_lines()` and treats the timeout as end-of-window, mirroring what
  `curl --max-time` does. **If you see the fallback warning, mid-call
  `ask_user` is dead and the demo loses its best moment — escalate.**

### Gotchas that bite during a demo

- **Persistence lag.** `outcome_type` / `summary` / `transcript_full` populate
  **20–30s AFTER** `status` becomes `completed`. Reading immediately gives
  nulls. Drive the wrist buzz off `follow()` (returns at call end); use
  `result()` only for the dashboard.
- **402 at `remaining < 200`.** Every dial *reserves* 200 credits and refunds
  the remainder. So 402 fires with a non-zero balance. Run `preflight` before
  going on stage. Top-ups are manual — ask VOYGR.
- **Billing:** only `success_*` costs credits (10). Every `failed_*` is free, so
  failed rehearsals are free but successful ones aren't.
- **`failed_short_hangup` is the most common failure** — someone picks up,
  hears the mandatory AI disclosure, hangs up. Briefs front-load the name and
  reason into the first spoken sentence to fight this.
- **Limits:** 10 req/s, 100 req/min, ~2 concurrent calls (409 at the cap).
- **US destinations only.** Recordings are not downloadable — the transcript is
  the record, retained 7 days.

### Outcome taxonomy

Reached (billed): `success_booked`, `success_refused`, `success_no_booking`.
Not reached (free): `failed_short_hangup`, `failed_voicemail`,
`failed_no_answer`, `failed_busy`, `failed_no_agent_available`,
`failed_technical`.

Escalation branches on `CallResult.reached` — all six failure modes look
identical to the person who needs help: nobody is coming.

## Build order

1. ~~Callwright layer, dry-runnable~~ ✅
2. BLE stream into Python, printing accel
3. Snap detected reliably
4. **Snap → real phone rings. This is the minimum viable demo.**
5. Haptic menu + selection
6. Result buzzed back to the wrist
7. Camera context on the emergency branch (feeds `demo_data.LIVE_CONTEXT`)
8. Dashboard + rehearsal

Past 6 is optional. Hit 4 early, then improve.

## Conventions

- Python 3.11+, `uv` for everything. `[tool.uv] package = false` — no build
  step, run from the repo root.
- Comments explain *why*, especially where the code works around an API quirk.
  Don't strip those — they're the record of what we learned the hard way.
- Keep the call layer generic. Branch-specific logic lives in `briefs.py` and
  `flows.py`.
