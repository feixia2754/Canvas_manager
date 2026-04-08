"""Load and validate configuration from .env file."""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

# Carrier SMS gateway map
CARRIER_GATEWAYS = {
    "tmobile":  "tmomail.net",
    "t-mobile": "tmomail.net",
    "att":      "txt.att.net",
    "at&t":     "txt.att.net",
    "verizon":  "vtext.com",
    "sprint":   "messaging.sprintpcs.com",
    "uscellular": "email.uscc.net",
    "boost":    "sms.myboostmobile.com",
    "cricket":  "sms.cricketwireless.net",
    "metro":    "mymetropcs.com",
}


def _require(var: str, hint: str) -> str:
    value = os.getenv(var)
    if not value:
        print(f"Error: {var} not set.\n  {hint}")
        sys.exit(1)
    return value


def get_canvas_config() -> dict:
    return {
        "base_url": _require(
            "CANVAS_BASE_URL",
            "Set CANVAS_BASE_URL in your .env (e.g. https://canvas.cmu.edu)",
        ).rstrip("/"),
        "token": _require(
            "CANVAS_API_TOKEN",
            "Canvas → Account → Settings → New Access Token",
        ),
    }


def get_email_config() -> dict:
    return {
        "to_address": _require(
            "TO_EMAIL_ADDRESS",
            "Set TO_EMAIL_ADDRESS in your .env",
        ),
        "from_name": os.getenv("FROM_NAME", "Canvas Manager"),
    }


def get_sms_config() -> dict:
    """Return SMS gateway config derived from phone number + carrier."""
    phone_raw = _require(
        "TO_PHONE_NUMBER",
        "Set TO_PHONE_NUMBER in your .env (e.g. +19496897324)",
    )
    carrier = _require(
        "PHONE_CARRIER",
        "Set PHONE_CARRIER in your .env (e.g. tmobile, att, verizon)",
    ).lower().strip()

    gateway = CARRIER_GATEWAYS.get(carrier)
    if not gateway:
        print(
            f"Error: Unknown carrier '{carrier}'.\n"
            f"  Supported: {', '.join(CARRIER_GATEWAYS.keys())}"
        )
        sys.exit(1)

    # Strip non-digits from phone number
    digits = "".join(c for c in phone_raw if c.isdigit())
    if digits.startswith("1") and len(digits) == 11:
        digits = digits[1:]  # remove country code
    if len(digits) != 10:
        print(f"Error: TO_PHONE_NUMBER must be a 10-digit US number, got: {phone_raw}")
        sys.exit(1)

    return {
        "sms_email": f"{digits}@{gateway}",
        "phone": phone_raw,
        "carrier": carrier,
    }


def get_reminder_config() -> dict:
    return {
        "lookahead_days": int(os.getenv("REMINDER_LOOKAHEAD_DAYS", "3")),
        "reminder_time": os.getenv("REMINDER_TIME", "08:00"),
    }
