#!/usr/bin/env python3
"""
Pre-market validation: health, exercise entry, optional warning, Alpaca fill poll.

  python scripts/prep_market_open.py
  python scripts/prep_market_open.py --repeat 3
  python scripts/prep_market_open.py --skip-warning
  python scripts/prep_market_open.py --local   # http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
RENDER = "https://spy-options-bridge.onrender.com"
POLL_SEC = 2.0
FILL_TIMEOUT_SEC = 120


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV.exists():
        return out
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def alpaca_headers(env: dict[str, str]) -> dict[str, str]:
    return {
        "Apca-Api-Key-Id": env.get("APCA_API_KEY_ID") or env.get("ALPACA_API_KEY", ""),
        "Apca-Api-Secret-Key": env.get("APCA_API_SECRET_KEY") or env.get("ALPACA_SECRET_KEY", ""),
    }


def alpaca_base(env: dict[str, str]) -> str:
    return env.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")


def latest_spy(env: dict[str, str]) -> float:
    h = alpaca_headers(env)
    if not h["Apca-Api-Key-Id"]:
        return 590.0
    base = alpaca_base(env)
    try:
        q = httpx.get(f"{base}/v2/stocks/SPY/quotes/latest", headers=h, timeout=20)
        if q.is_success:
            ap = q.json().get("quote", {}).get("ap")
            if ap:
                return float(ap)
    except Exception:
        pass
    try:
        r = httpx.get(
            "https://data.alpaca.markets/v2/stocks/SPY/trades/latest",
            headers=h,
            timeout=20,
        )
        if r.is_success:
            p = r.json().get("trade", {}).get("p")
            if p:
                return float(p)
    except Exception:
        pass
    return 590.0


def market_clock(env: dict[str, str]) -> dict:
    h = alpaca_headers(env)
    if not h["Apca-Api-Key-Id"]:
        return {}
    r = httpx.get(f"{alpaca_base(env)}/v2/clock", headers=h, timeout=20)
    return r.json() if r.is_success else {}


def poll_mleg_fill(env: dict[str, str], order_id: str | None, timeout_sec: float) -> tuple[bool, str]:
    h = alpaca_headers(env)
    if not h["Apca-Api-Key-Id"]:
        return False, "no Alpaca keys in .env — cannot poll"
    base = alpaca_base(env)
    deadline = time.time() + timeout_sec
    last_status = "unknown"
    while time.time() < deadline:
        if order_id:
            r = httpx.get(f"{base}/v2/orders/{order_id}", headers=h, timeout=20)
            if r.is_success:
                o = r.json()
                last_status = str(o.get("status", last_status))
                if last_status == "filled":
                    return True, f"filled order {order_id[:8]}…"
                if last_status in {"canceled", "expired", "rejected", "failed"}:
                    return False, f"order {last_status}"
        r2 = httpx.get(
            f"{base}/v2/orders",
            headers=h,
            params={"status": "all", "limit": 8, "direction": "desc"},
            timeout=20,
        )
        if r2.is_success:
            for o in r2.json():
                if o.get("order_class") != "mleg":
                    continue
                st = o.get("status")
                oid = o.get("id", "")
                if order_id and oid != order_id:
                    continue
                last_status = str(st)
                if st == "filled":
                    return True, f"filled mleg {oid[:8]}… limit={o.get('limit_price')}"
        time.sleep(POLL_SEC)
    return False, f"timeout (last status={last_status}); market may be closed"


def run_once(
    base_url: str,
    env: dict[str, str],
    *,
    test_warning: bool,
    fill_timeout: float = FILL_TIMEOUT_SEC,
) -> int:
    secret = env.get("WEBHOOK_SECRET", "")
    if not secret:
        print("FAIL: WEBHOOK_SECRET missing in .env")
        return 1

    fails = 0
    clock = market_clock(env)
    if clock:
        print(f"Market: is_open={clock.get('is_open')} next_open={clock.get('next_open')}")
    spy = latest_spy(env)
    print(f"SPY reference price: {spy:.2f}")

    hr = httpx.get(f"{base_url}/health", timeout=30)
    if not hr.is_success:
        print(f"FAIL health HTTP {hr.status_code}")
        return 1
    health = hr.json()
    ver = health.get("version", "?")
    print(f"health version={ver} configured={health.get('configured')} paper_force={health.get('paper_force_min_fill')}")
    if str(ver) < "5.5.3":
        print(f"WARN: expected version >= 5.5.3 (got {ver}) — deploy Render after push")

    body = {
        "webhookSecret": secret,
        "ticker": "SPY",
        "action": "PUT_CREDIT_SPREAD",
        "signalPrice": spy,
        "dteFilter": "weekly",
        "strikeOffsetShort": -5,
        "strikeOffsetLong": -6,
        "quantity": 1,
        "fillMode": "exercise",
        "limitCredit": 0.55,
    }
    entry_paths = ["/entry", "/exercise/entry"]
    er = None
    data: dict = {}
    for path in entry_paths:
        print(f"POST {base_url}{path} …")
        er = httpx.post(f"{base_url}{path}", json=body, timeout=120)
        if er.status_code == 404 and path != entry_paths[-1]:
            print(f"  HTTP 404 on {path} — trying next endpoint")
            continue
        try:
            data = er.json()
        except Exception:
            data = {}
        break
    if er is None:
        print("FAIL: no entry response")
        return 1
    print(f"  HTTP {er.status_code} success={data.get('success')} msg={data.get('message')}")
    order_id = data.get("order_id") or data.get("orderId")
    if order_id:
        print(f"  order_id={order_id}")
    if not data.get("success"):
        print("FAIL: exercise entry rejected")
        fails += 1
    else:
        ok, msg = poll_mleg_fill(env, str(order_id) if order_id else None, fill_timeout)
        if ok:
            print(f"PASS entry fill: {msg}")
        else:
            print(f"WARN entry fill: {msg}")
            if not clock.get("is_open"):
                print("  (expected if market closed — order may fill at Thursday open)")
            else:
                fails += 1

    if test_warning:
        atm = round(spy)
        short_est = atm + (-5)
        danger_price = short_est * 0.995
        wbody = {
            "webhookSecret": secret,
            "ticker": "SPY",
            "signalPrice": danger_price,
            "strikeOffsetShort": -5,
            "strikeOffsetLong": -6,
            "overrideAutoClose": True,
        }
        print(f"POST {base_url}/warning (notify-only test) danger_price={danger_price:.2f} …")
        wr = httpx.post(f"{base_url}/warning", json=wbody, timeout=60)
        wd = wr.json()
        print(f"  danger_zone={wd.get('danger_zone')} action={wd.get('action_taken')}")
        if wd.get("danger_zone"):
            print("PASS warning endpoint responded in danger zone")
        else:
            print("WARN warning did not flag danger — check strike offsets")
            fails += 1

    return fails


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-market bridge + Alpaca paper validation")
    parser.add_argument("--repeat", type=int, default=1, help="Run N consecutive tests")
    parser.add_argument("--skip-warning", action="store_true", help="Skip /warning probe")
    parser.add_argument("--local", action="store_true", help="Use http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=FILL_TIMEOUT_SEC, help="Fill poll seconds")
    args = parser.parse_args()
    fill_timeout = args.timeout

    env = load_env()
    base = "http://127.0.0.1:8000" if args.local else RENDER
    test_warning = not args.skip_warning

    total_fail = 0
    for i in range(args.repeat):
        if args.repeat > 1:
            print(f"\n=== Run {i + 1}/{args.repeat} ===")
        total_fail += run_once(base, env, test_warning=test_warning, fill_timeout=fill_timeout)
        if i + 1 < args.repeat:
            time.sleep(5)

    if total_fail:
        print(f"\nRESULT: {total_fail} failure(s) across {args.repeat} run(s)")
        return 1
    print(f"\nRESULT: PASS ({args.repeat} run(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
