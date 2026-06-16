#!/usr/bin/env python3
"""Safe Render pipe verify — no orders. Proves TV webhooks can reach bridge + auth gates work."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
BRIDGE = "https://spy-options-bridge.onrender.com"
OUT = Path(r"C:\Users\Shiel\Desktop\BRIDGE-PIPE-TEST.txt")
ET = ZoneInfo("America/New_York")


def load_secret() -> str:
    if not ENV.exists():
        return ""
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("WEBHOOK_SECRET="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def line(lines: list[str], msg: str) -> None:
    print(msg)
    lines.append(msg)


def main() -> int:
    lines: list[str] = []
    now = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    secret = load_secret()
    fails = 0

    line(lines, f"BRIDGE PIPE TEST — {now}")
    line(lines, "=" * 55)
    line(lines, "")
    line(lines, "Safe test: NO new SPY/STX orders placed.")
    line(lines, "")

    # 1) Health
    try:
        h = httpx.get(f"{BRIDGE}/health", timeout=60).json()
        risk = h.get("tv_pause_risk") or {}
        level = risk.get("level", "?")
        line(lines, f"[1] HEALTH .............. {'PASS' if h.get('status') == 'ok' else 'FAIL'}")
        line(lines, f"    version={h.get('version')} broker={h.get('broker_label')}")
        line(lines, f"    tv_pause_risk={level} reasons={risk.get('reasons') or 'none'}")
        line(lines, f"    trade_cap={h.get('spread_max_trades_per_day')} loss_limit={h.get('spread_daily_loss_limit')}")
        line(lines, f"    min_credit={h.get('spread_min_credit')} email={h.get('email_enabled')}")
        if level == "red":
            fails += 1
            line(lines, "    ** RED = pause TV until fixed **")
        elif level == "yellow":
            line(lines, "    ** YELLOW = watch open orders / cold start **")
    except Exception as exc:
        fails += 1
        line(lines, f"[1] HEALTH .............. FAIL ({exc})")

    # 2) Ping
    try:
        p = httpx.get(f"{BRIDGE}/ping", timeout=30).json()
        ok = p.get("status") == "ok"
        line(lines, f"[2] PING ................ {'PASS' if ok else 'FAIL'}")
        if not ok:
            fails += 1
    except Exception as exc:
        fails += 1
        line(lines, f"[2] PING ................ FAIL ({exc})")

    # 3) Auth gate — bad secret must 401
    for name, url, body in [
        ("ENTRY bad secret", f"{BRIDGE}/entry", {"webhookSecret": "wrong", "ticker": "SPY", "action": "PUT_CREDIT_SPREAD", "signalPrice": 600}),
        ("WARNING bad secret", f"{BRIDGE}/warning", {"webhookSecret": "wrong", "ticker": "SPY", "signalPrice": 600, "strikeOffsetShort": -10, "strikeOffsetLong": -15}),
        ("STX bad secret", f"{BRIDGE}/webhook/stx-close", {"webhookSecret": "wrong", "underlying": "STX", "mode": "evaluate"}),
    ]:
        try:
            r = httpx.post(url, json=body, timeout=45)
            ok = r.status_code == 401
            line(lines, f"[3] {name:20} {'PASS (401)' if ok else f'FAIL ({r.status_code})'}")
            if not ok:
                fails += 1
        except Exception as exc:
            fails += 1
            line(lines, f"[3] {name:20} FAIL ({exc})")

    # 4) Good secret — read-only STX evaluate (proves JSON path works)
    if not secret:
        fails += 1
        line(lines, "[4] STX evaluate ........ SKIP (no WEBHOOK_SECRET in .env)")
    else:
        try:
            r = httpx.post(
                f"{BRIDGE}/webhook/stx-close",
                json={
                    "webhookSecret": secret,
                    "underlying": "STX",
                    "mode": "evaluate",
                    "strike": 230,
                    "dteFilter": "weekly",
                    "type": "put",
                },
                timeout=60,
            )
            data = r.json()
            ok = r.is_success and data.get("success") is not False
            line(lines, f"[4] STX evaluate ........ {'PASS' if ok else 'FAIL'} HTTP {r.status_code}")
            line(lines, f"    action={data.get('action_taken')} msg={str(data.get('message',''))[:80]}")
            if not ok:
                fails += 1
        except Exception as exc:
            fails += 1
            line(lines, f"[4] STX evaluate ........ FAIL ({exc})")

    # 5) Activity log reachable
    try:
        a = httpx.get(f"{BRIDGE}/activity", timeout=45).json()
        line(lines, f"[5] ACTIVITY log ........ PASS ({a.get('count', 0)} events today)")
    except Exception as exc:
        fails += 1
        line(lines, f"[5] ACTIVITY log ........ FAIL ({exc})")

    line(lines, "")
    line(lines, "WHAT CAN BLOCK YOU SILENTLY (TV still says Delivered):")
    line(lines, "  - spread_min_credit skip (check /activity for 'skipped')")
    line(lines, "  - daily loss limit if re-enabled")
    line(lines, "  - chart not firing (no webhook at all — not Render)")
    line(lines, "  - Render cold sleep: first webhook slow ~30-60s (use K keepalive)")
    line(lines, "")
    line(lines, "YOU GET EMAIL when bridge sends notify (fills, errors, health FAIL).")
    line(lines, "SKIPS log to /activity but may NOT email — check Commander A or this test.")
    line(lines, "")
    if fails == 0:
        line(lines, "RESULT: ALL PASS — Render is receiving webhooks as designed.")
    else:
        line(lines, f"RESULT: {fails} check(s) FAILED — see above.")
    line(lines, "")
    line(lines, f"JSON health: {BRIDGE}/health")
    line(lines, f"JSON activity: {BRIDGE}/activity")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
