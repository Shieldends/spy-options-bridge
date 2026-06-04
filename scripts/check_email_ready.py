#!/usr/bin/env python3
"""Report whether local email is ready to test (no secrets printed)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from email_alerts import _load_dotenv, email_configured, normalize_smtp_password  # noqa: E402

REQUIRED = (
    "EMAIL_ENABLED",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "EMAIL_FROM",
    "EMAIL_TO",
)


def main() -> int:
    _load_dotenv()
    print("Email config check (no passwords shown):\n")
    missing = [k for k in REQUIRED if not (os.getenv(k) or "").strip()]
    for k in REQUIRED:
        print(f"  {k}: {'SET' if k not in missing else 'MISSING'}")
    pwd_len = len(normalize_smtp_password(os.getenv("SMTP_PASSWORD") or ""))
    print(f"  SMTP_PASSWORD length: {pwd_len} (Google app password should be 16)")
    print(f"  email_configured(): {email_configured()}")
    if missing:
        print("\nFAIL — run PASTE-EMAIL-PASSWORD.bat on Desktop")
        return 1
    if pwd_len != 16:
        print(
            "\nFAIL — paste ONLY the 16 letters from Google (not your Gmail login password).\n"
            "Fix: double-click Desktop\\PASTE-EMAIL-PASSWORD.bat → paste → SAVE"
        )
        return 1
    print("\nConfig OK — run TEST-EMAIL-NOW.bat (inbox must show [SPY Command Center])")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
