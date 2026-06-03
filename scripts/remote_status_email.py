#!/usr/bin/env python3
"""Daily bridge health email — run from Task Scheduler or manually with --alert."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from email_alerts import send_email_alert  # noqa: E402

HEALTH_URL = "https://spy-options-bridge.onrender.com/health"
MIN_VERSION = "5.5.8"
ET = ZoneInfo("America/New_York")


def fetch_health() -> tuple[bool, dict]:
    try:
        r = httpx.get(HEALTH_URL, timeout=30)
        data = r.json() if r.is_success else {}
        return r.is_success, data
    except Exception as exc:
        return False, {"error": str(exc)}


def version_ok(data: dict) -> bool:
    ver = str(data.get("version", "0"))
    return ver >= MIN_VERSION


def main() -> int:
    parser = argparse.ArgumentParser(description="Email bridge health status")
    parser.add_argument("--alert", action="store_true", help="Send only on failure (for keepalive/Task Scheduler)")
    parser.add_argument("--test", action="store_true", help="Send test email to EMAIL_TO (no health check)")
    args = parser.parse_args()

    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")

    if args.test:
        body = (
            f"SPY bridge test email.\n\n"
            f"Time: {ts}\n"
            f"If you received this, local SMTP / Render EMAIL_* is working.\n"
            f"Recipient default: shieldinc850@gmail.com\n"
        )
        sent = send_email_alert("SPY Bridge TEST email", body)
        print("test email sent" if sent else "email disabled or failed — run SETUP-EMAIL-AUTOMATION.bat first")
        return 0 if sent else 1

    ok, data = fetch_health()
    configured = str(data.get("configured", "false")).lower() == "true"
    ver = data.get("version", "?")
    healthy = ok and version_ok(data) and configured

    if healthy and not args.alert:
        body = (
            f"spy-options-bridge is ready.\n\n"
            f"Time: {ts}\n"
            f"Version: {ver}\n"
            f"Configured: {configured}\n"
            f"Broker: {data.get('broker_label', data.get('broker', '?'))}\n"
            f"URL: {HEALTH_URL}\n\n"
            f"TradingView → Render → Alpaca runs without your PC when Render is up."
        )
        sent = send_email_alert(f"SPY Bridge ready ({ver})", body)
        print("daily-ready email sent" if sent else "email disabled or failed (see logs)")
        return 0

    if not healthy:
        err = data.get("error", "")
        body = (
            f"Bridge health check FAILED.\n\n"
            f"Time: {ts}\n"
            f"HTTP ok: {ok}\n"
            f"Version: {ver} (need >={MIN_VERSION})\n"
            f"Configured: {configured}\n"
            f"Error: {err}\n"
            f"URL: {HEALTH_URL}\n"
        )
        send_email_alert("SPY Bridge ALERT — health failed", body)
        print("alert email sent (or disabled)")
        return 1

    print("health OK — --alert mode, no email")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
