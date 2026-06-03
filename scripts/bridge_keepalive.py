#!/usr/bin/env python3
"""Ping Render /ping every 5 minutes during 9:00–16:00 ET (paper bridge warm)."""
from __future__ import annotations

import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

RENDER_PING = "https://spy-options-bridge.onrender.com/ping"
INTERVAL_SEC = 300
ET = ZoneInfo("America/New_York")


def in_market_hours() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    return 9 <= now.hour < 16


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
    print("bridge_keepalive: /ping every 5 min, active 9:00–16:00 ET Mon–Fri")
    print(f"target={RENDER_PING}")
    while True:
        if in_market_hours():
            ping_once()
            time.sleep(INTERVAL_SEC)
        else:
            ts = datetime.now(ET).strftime("%H:%M:%S ET")
            print(f"{ts} outside 9–16 ET — sleep 60s")
            time.sleep(60)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nkeepalive stopped")
        raise SystemExit(0)
