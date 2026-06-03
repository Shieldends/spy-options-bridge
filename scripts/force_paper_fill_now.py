#!/usr/bin/env python3
"""Fire one paper entry via Render and poll Alpaca until filled or timeout."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"


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


def main() -> int:
    env = load_env()
    secret = env.get("WEBHOOK_SECRET", "")
    key = env.get("APCA_API_KEY_ID") or env.get("ALPACA_API_KEY", "")
    sec = env.get("APCA_API_SECRET_KEY") or env.get("ALPACA_SECRET_KEY", "")
    base = env.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")

    if not secret:
        print("WEBHOOK_SECRET missing in .env")
        return 1

    # Live SPY price from Alpaca
    signal_price = 590.0
    if key and sec:
        h = {"Apca-Api-Key-Id": key, "Apca-Api-Secret-Key": sec}
        try:
            q = httpx.get(f"{base}/v2/stocks/SPY/quotes/latest", headers=h, timeout=20)
            if q.is_success:
                ap = q.json().get("quote", {}).get("ap")
                if ap:
                    signal_price = float(ap)
        except Exception as exc:
            print("quote fallback:", exc)

    body = {
        "webhookSecret": secret,
        "ticker": "SPY",
        "action": "PUT_CREDIT_SPREAD",
        "signalPrice": signal_price,
        "dteFilter": "weekly",
        "strikeOffsetShort": -5,
        "strikeOffsetLong": -6,
        "quantity": 1,
        "fillMode": "exercise",
        "limitCredit": 0.55,
    }
    print(f"POST /entry signalPrice={signal_price}")
    r = httpx.post("https://spy-options-bridge.onrender.com/entry", json=body, timeout=120)
    data = r.json()
    print("HTTP", r.status_code, data.get("message"))

    if not key or not sec:
        print("No Alpaca keys in .env — check Alpaca orders manually in ~30s")
        return 0

    h = {"Apca-Api-Key-Id": key, "Apca-Api-Secret-Key": sec}
    deadline = time.time() + 90
    last_id = None
    while time.time() < deadline:
        orr = httpx.get(f"{base}/v2/orders?status=all&limit=5&direction=desc", headers=h, timeout=20)
        if orr.is_success:
            for o in orr.json():
                if o.get("order_class") != "mleg":
                    continue
                oid = o.get("id")
                st = o.get("status")
                lp = o.get("limit_price")
                if oid != last_id:
                    print(f"order {oid[:8]}… status={st} limit={lp}")
                    last_id = oid
                if st == "filled":
                    print("FILLED — check Alpaca Positions tab")
                    return 0
        time.sleep(4)

    print("Not filled in 90s — check Alpaca Orders (may still be new near close)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
