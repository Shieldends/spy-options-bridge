#!/usr/bin/env python3
"""Ping Render /ping every 5 minutes on weekdays (24h ET) to limit free-tier cold sleep."""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

from team_email import notify_health_fail  # noqa: E402

RENDER_PING = "https://spy-options-bridge.onrender.com/ping"
RENDER_WAKE = "https://spy-options-bridge.onrender.com/health"
INTERVAL_SEC = 300
PING_TIMEOUTS = (30.0, 60.0, 90.0)
FAIL_EMAIL_THRESHOLD = 3
ET = ZoneInfo("America/New_York")


def should_ping() -> bool:
    """Weekdays only — ping overnight so TV webhooks are not first cold-start victim."""
    return datetime.now(ET).weekday() < 5


def ping_once() -> bool:
    last_exc: Exception | None = None
    for timeout in PING_TIMEOUTS:
        try:
            httpx.get(RENDER_WAKE, timeout=timeout)
            r = httpx.get(RENDER_PING, timeout=timeout)
            data = r.json() if r.is_success else {}
            ver = data.get("version", "?")
            ts = datetime.now(ET).strftime("%H:%M:%S ET")
            print(f"{ts} ping HTTP {r.status_code} version={ver}")
            return r.is_success
        except Exception as exc:
            last_exc = exc
    ts = datetime.now(ET).strftime("%H:%M:%S ET")
    print(f"{ts} ping FAIL: {last_exc}")
    return False


def main() -> int:
    print("bridge_keepalive: /ping every 5 min, active Mon–Fri 24h ET")
    print(f"target={RENDER_PING}")
    consecutive_fails = 0
    while True:
        if should_ping():
            if ping_once():
                consecutive_fails = 0
            else:
                consecutive_fails += 1
                if consecutive_fails >= FAIL_EMAIL_THRESHOLD:
                    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
                    notify_health_fail(
                        f"Render /ping failed {consecutive_fails} times in a row. "
                        f"Time: {ts}. URL: {RENDER_PING}. "
                        "Check Render dashboard or Manual Deploy."
                    )
                    consecutive_fails = 0
            time.sleep(INTERVAL_SEC)
        else:
            consecutive_fails = 0
            ts = datetime.now(ET).strftime("%H:%M:%S ET")
            print(f"{ts} weekend — sleep 60s")
            time.sleep(60)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nkeepalive stopped")
        raise SystemExit(0)
