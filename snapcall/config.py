"""Environment + safety configuration.

The allow-list is the important part. During a hackathon a typo in a phone
number places a real call to a real stranger and burns credits. Every dial goes
through `assert_dialable` first.
"""

from __future__ import annotations

import os
import re

from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("CALLWRIGHT_BASE_URL", "https://api.voygr.tech")
API_KEY = os.getenv("CALLWRIGHT_API_KEY", "").strip()

# Dry run defaults to ON. You have to deliberately turn it off to ring a phone.
DRY_RUN = os.getenv("CALLWRIGHT_DRY_RUN", "1").strip().lower() not in ("0", "false", "no", "")

E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def _numbers(raw: str) -> list[str]:
    return [n.strip() for n in raw.split(",") if n.strip()]


ALLOWED_NUMBERS = _numbers(os.getenv("CALLWRIGHT_ALLOWED_NUMBERS", ""))

CAREGIVER_PRIMARY_PHONE = os.getenv("CAREGIVER_PRIMARY_PHONE", "").strip()
CAREGIVER_BACKUP_PHONE = os.getenv("CAREGIVER_BACKUP_PHONE", "").strip()
ERRAND_PHONE = os.getenv("ERRAND_PHONE", "").strip()


class ConfigError(RuntimeError):
    pass


def assert_key() -> str:
    if not API_KEY:
        raise ConfigError(
            "CALLWRIGHT_API_KEY is not set. Copy .env.example to .env and paste "
            "the team key from the VOYGR spreadsheet 'Keys' tab."
        )
    return API_KEY


def assert_dialable(phone: str) -> str:
    """Raise unless `phone` is well-formed and on the allow-list."""
    phone = phone.strip()
    if not E164.match(phone):
        raise ConfigError(f"{phone!r} is not E.164 (needs a leading + and country code, e.g. +14155550199)")
    if ALLOWED_NUMBERS and phone not in ALLOWED_NUMBERS:
        raise ConfigError(
            f"{phone} is not in CALLWRIGHT_ALLOWED_NUMBERS. Add it to .env if you "
            f"really mean to dial it. Currently allowed: {', '.join(ALLOWED_NUMBERS) or '(none)'}"
        )
    return phone


def redact(key: str) -> str:
    """Safe-to-log form of an API key."""
    if len(key) <= 12:
        return "***"
    return f"{key[:8]}...{key[-4:]}"
