#!/usr/bin/env python3
"""
End-to-end system exercise: Render bridge + Alpaca paper + warning path.
Designed for validation (not profit). Run via Desktop RUN-SYSTEM-EXERCISE.bat.

  python exercise_system.py          # full run + auto undo at end
  python exercise_system.py --undo   # cancel orders + close exercise spreads only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
DESKTOP_REPORT = Path.home() / "Desktop" / "EXERCISE-RESULT.txt"
ET = ZoneInfo("America/New_York")

BRIDGE = "https://spy-options-bridge.onrender.com"
POLL_SEC = 2
FILL_WAIT_SEC = 120


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def log(lines: list[str], msg: str) -> None:
    line = f"[{datetime.now(tz=ET).strftime('%H:%M:%S')}] {msg}"
    print(line)
    lines.append(line)


def alpaca_headers(env: dict[str, str]) -> dict[str, str]:
    return {
        "Apca-Api-Key-Id": env["APCA_API_KEY_ID"],
        "Apca-Api-Secret-Key": env["APCA_API_SECRET_KEY"],
    }


def alpaca_base(env: dict[str, str]) -> str:
    return env.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")


def market_clock(env: dict[str, str]) -> dict:
    r = httpx.get(f"{alpaca_base(env)}/v2/clock", headers=alpaca_headers(env), timeout=20)
    return r.json() if r.is_success else {}


def latest_spy(env: dict[str, str]) -> float:
    h = alpaca_headers(env)
    r = httpx.get(
        "https://data.alpaca.markets/v2/stocks/SPY/trades/latest",
        headers=h,
        timeout=20,
    )
    if r.is_success:
        p = r.json().get("trade", {}).get("p")
        if p:
            return float(p)
    r2 = httpx.get(
        "https://data.alpaca.markets/v2/stocks/SPY/bars",
        headers=h,
        params={"timeframe": "1Min", "limit": 1},
        timeout=20,
    )
    bars = r2.json().get("bars") or []
    if bars:
        return float(bars[-1]["c"])
    return 590.0


def cancel_open_mleg(env: dict[str, str], lines: list[str]) -> int:
    base = alpaca_base(env)
    h = alpaca_headers(env)
    r = httpx.get(f"{base}/v2/orders", headers=h, params={"status": "open", "nested": True}, timeout=30)
    if not r.is_success:
        log(lines, f"FAIL cancel list: HTTP {r.status_code}")
        return 0
    n = 0
    for o in r.json():
        if o.get("order_class") != "mleg":
            continue
        oid = o.get("id")
        if not oid:
            continue
        cr = httpx.delete(f"{base}/v2/orders/{oid}", headers=h, timeout=30)
        if cr.is_success:
            n += 1
            log(lines, f"Canceled order {oid[:8]}… limit={o.get('limit_price')}")
    if n == 0:
        log(lines, "No open multi-leg orders to cancel")
    return n


def close_spy_put_spreads_via_warning(env: dict[str, str], secret: str, lines: list[str]) -> int:
    """Emergency close SPY put spreads via /warning (must use danger-zone price)."""
    base = alpaca_base(env)
    h = alpaca_headers(env)
    r = httpx.get(f"{base}/v2/positions", headers=h, timeout=30)
    if not r.is_success:
        log(lines, "FAIL positions fetch")
        return 0
    has_spy_option = any(
        (p.get("symbol") or "").upper().startswith("SPY") and float(p.get("qty") or 0) != 0
        for p in r.json()
    )
    if not has_spy_option:
        log(lines, "No SPY option positions to close")
        return 0

    spot = latest_spy(env)
    short_est = round(spot) - 5
    danger_price = short_est * 0.999
    body = {
        "webhookSecret": secret,
        "ticker": "SPY",
        "signalPrice": danger_price,
        "strikeOffsetShort": -5,
        "strikeOffsetLong": -6,
        "overrideAutoClose": False,
        "forceAutoClose": True,
    }
    wr = httpx.post(f"{BRIDGE}/warning", json=body, timeout=90)
    data = wr.json() if wr.headers.get("content-type", "").startswith("application/json") else {}
    log(lines, f"Warning close HTTP {wr.status_code} action={data.get('action_taken')} matched={data.get('positions_matched')}")
    return 1 if wr.is_success and data.get("action_taken", "").startswith(("closed", "submitted")) else 0


def poll_order(env: dict[str, str], order_id: str, lines: list[str]) -> str:
    base = alpaca_base(env)
    h = alpaca_headers(env)
    deadline = time.time() + FILL_WAIT_SEC
    last = ""
    while time.time() < deadline:
        r = httpx.get(f"{base}/v2/orders/{order_id}", headers=h, timeout=20)
        if r.is_success:
            last = (r.json().get("status") or "").lower()
            log(lines, f"Order status: {last}")
            if last in {"filled", "partially_filled"}:
                return last
            if last in {"canceled", "expired", "rejected", "failed"}:
                return last
        time.sleep(POLL_SEC)
    return last or "timeout"


def run_exercise(env: dict[str, str], lines: list[str]) -> int:
    secret = env.get("WEBHOOK_SECRET", "")
    if not secret:
        log(lines, "FAIL: WEBHOOK_SECRET missing in .env")
        return 1

    clock = market_clock(env)
    if clock:
        log(lines, f"Market open={clock.get('is_open')} next_open={clock.get('next_open')}")
        if not clock.get("is_open"):
            log(lines, "WARN: market closed — fills may not happen (exercise still tests webhooks)")

    hr = httpx.get(f"{BRIDGE}/health", timeout=30)
    if hr.is_success:
        h = hr.json()
        log(lines, f"PASS health version={h.get('version')} broker={h.get('broker_label')}")
    else:
        log(lines, f"FAIL health HTTP {hr.status_code}")
        return 1

    log(lines, "UNDO: clearing open orders before exercise…")
    cancel_open_mleg(env, lines)

    spot = latest_spy(env)
    log(lines, f"SPY spot ≈ {spot:.2f}")

    entry_body = {
        "webhookSecret": secret,
        "ticker": "SPY",
        "action": "PUT_CREDIT_SPREAD",
        "signalPrice": spot,
        "dteFilter": "weekly",
        "strikeOffsetShort": -5,
        "strikeOffsetLong": -6,
        "quantity": 1,
        "fillMode": "exercise",
        "limitCredit": 0.55,
    }
    log(lines, "ENTRY: POST /entry (fillMode=exercise)…")
    er = httpx.post(f"{BRIDGE}/entry", json=entry_body, timeout=90)
    if not er.is_success:
        log(lines, f"FAIL entry HTTP {er.status_code} {er.text[:200]}")
        return 1
    entry = er.json()
    if not entry.get("success"):
        log(lines, f"FAIL entry: {entry.get('message')}")
        return 1
    br = (entry.get("entry") or {}).get("broker_response") or {}
    oid = br.get("id")
    limit = (entry.get("entry") or {}).get("payload", {}).get("limit_price")
    log(lines, f"PASS entry accepted id={oid} limit={limit}")

    fill_status = "skipped"
    if oid:
        fill_status = poll_order(env, oid, lines)
    if fill_status == "filled":
        log(lines, "PASS entry FILLED")
        time.sleep(8)
        ords = httpx.get(
            f"{alpaca_base(env)}/v2/orders",
            headers=alpaca_headers(env),
            params={"status": "open", "nested": True},
            timeout=30,
        )
        if ords.is_success:
            n = len([o for o in ords.json() if o.get("order_class") == "mleg"])
            log(lines, f"Open mleg orders after fill (TP/SL may appear): {n}")
    elif fill_status in {"new", "pending_new", "accepted", "timeout"}:
        log(lines, f"WARN entry not filled (status={fill_status}) — limit may be off market")
    else:
        log(lines, f"WARN entry ended: {fill_status}")

    short_est = round(spot) - 5
    danger_price = short_est * 0.999
    warn_notify = {
        "webhookSecret": secret,
        "ticker": "SPY",
        "signalPrice": danger_price,
        "strikeOffsetShort": -5,
        "strikeOffsetLong": -6,
        "overrideAutoClose": True,
    }
    log(lines, "WARNING: POST /warning (overrideAutoClose=true, no close)…")
    wr = httpx.post(f"{BRIDGE}/warning", json=warn_notify, timeout=90)
    if wr.is_success and wr.json().get("action_taken"):
        log(lines, f"PASS warning notify action={wr.json().get('action_taken')}")
    else:
        log(lines, f"WARN warning HTTP {wr.status_code}")

    log(lines, "UNDO: auto cleanup (cancel orders + close spreads)…")
    cancel_open_mleg(env, lines)
    close_spy_put_spreads_via_warning(env, secret, lines)
    cancel_open_mleg(env, lines)

    log(lines, "DONE — paper account should be flat for exercise artifacts")
    return 0


def undo_only(env: dict[str, str], lines: list[str]) -> int:
    secret = env.get("WEBHOOK_SECRET", "")
    cancel_open_mleg(env, lines)
    if secret:
        close_spy_put_spreads_via_warning(env, secret, lines)
    cancel_open_mleg(env, lines)
    log(lines, "UNDO complete")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SPY bridge system exercise")
    parser.add_argument("--undo", action="store_true", help="Only undo (cancel orders, close spreads)")
    args = parser.parse_args()
    lines: list[str] = []
    log(lines, "=== SPY OPTIONS BRIDGE — SYSTEM EXERCISE ===")
    env = load_env()
    code = undo_only(env, lines) if args.undo else run_exercise(env, lines)
    DESKTOP_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(lines, f"Report saved: {DESKTOP_REPORT}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())