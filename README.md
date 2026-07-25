# SnapCall

**A wristband gesture places a real phone call.**

Built at c0mpiled-11 (YC Startup School Hackathon) on the Callwright API by
VOYGR (YC W26).

Tens of millions of everyday tasks are locked behind a phone call — refilling a
prescription, booking an appointment, asking a front desk for help. If you can't
use a phone, you can't do any of them. SnapCall gives a phone to people who
can't use one: older adults, nonverbal people, anyone who can't work a
smartphone.

Snap your fingers. The band buzzes. A real call gets placed, a real conversation
happens, and the result buzzes back to your wrist.

*She didn't touch a screen, didn't say a word, and got what she needed.*

## How it works

```
snap  ──▶  laptop detects  ──▶  brief built  ──▶  Callwright dials
                                                        │
                          wrist buzz  ◀── outcome ◀──────┤
                                                        │
                          "where is she?" ──────────────▶│
                          camera caption ◀───────────────┘
```

The last loop is the interesting one. Mid-call, the voice agent can ask *our
system* a question and speak the answer back to whoever's on the line. A
caregiver asks "where is she?" and hears the live camera caption — "she's on the
floor in the kitchen" — during the call.

## Quickstart

```sh
cd backend
uv sync
cp .env.example .env          # paste your Callwright key
uv run python -m snapcall.cli preflight
uv run python -m snapcall.cli emergency     # dry run — nothing dials
```

`backend/` is the whole laptop side — camera pipeline and call layer in one
self-contained uv project. `firmware/` is the ESP32 sketch and shares nothing
with it.

The real thing, camera and all:

```sh
uv sync --extra vision
uv run python -m snapcall.vision.detector --address <esp32-ip>
```

Everything is dry run by default and simulates the full event stream, including
a mid-call question. Pass `--live` to actually ring a phone.

## Safety

- `.env` is gitignored. The API key is never committed or logged.
- Every dial is checked against `CALLWRIGHT_ALLOWED_NUMBERS`.
- Real calls cost credits and ring real phones. Only dial numbers you're
  authorized to call.
- **Not a certified medical alert device.** A prototype, and not anyone's sole
  lifeline. The emergency briefs explicitly redirect to 911.

See [CLAUDE.md](CLAUDE.md) for architecture and API notes.
