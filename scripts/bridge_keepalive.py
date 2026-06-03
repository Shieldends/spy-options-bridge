#!/usr/bin/env python3
"""Ping Render /ping every 5 minutes during 8:00–17:00 ET (paper bridge warm)."""
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
INTERVAL_SEC = 300
FAIL_EMAIL_THRESHOLD = 3
ET = ZoneInfo("America/New_York")


def in_market_hours() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    return 8 <= now.hour < 17


def ping_once() -> bool:
    try:
        r = httpx.get(RENDER_PING, timeout=30)
        data = r.json() if r.is_success else {}
        ver = data.get("version", "?")
        ts = datetime.now(ET).strftime("%H:%M:%S ET")
        print(f"{ts} ping HTTP {r.status_code} version={ver}")
        return r.is_success
    except Exception as exc:
        ts = datetime.now(ET).strftime("%H:%M:%S ET")
        print(f"{ts} ping FAIL: {exc}")
        return False


def main() -> int:
    print("bridge_keepalive: /ping every 5 min, active 8:00–17:00 ET Mon–Fri")
    print(f"target={RENDER_PING}")
    consecutive_fails = 0
    while True:
        if in_market_hours():
            if ping_once():
                consecutive_fails = 0
            else:
                consecutive_fails += 1
                if consecutive_fails >= FAIL_EMAIL_THRESHOLD:
                    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
                    notify_health_fail(
                        f"Render /ping failed {consecutive_fails} times in a row. "
                        f"Time: {ts}. URL: {RENDER_PING}. "
                        "Check Render or wait for cold-start recovery."
                    )
                    consecutive_fails = 0
            time.sleep(INTERVAL_SEC)
        else:
            consecutive_fails = 0
            ts = datetime.now(ET).strftime("%H:%M:%S ET")
            print(f"{ts} outside 8–17 ET — sleep 60s")
            time.sleep(60)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nkeepalive stopped")
        raise SystemExit(0)
