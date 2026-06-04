#!/usr/bin/env python3
"""One-shot test: TradingView-style POST to Render /entry (Alpaca paper)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
URL = "https://spy-options-bridge.onrender.com/entry"


def load_secret() -> str:
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("WEBHOOK_SECRET="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("WEBHOOK_SECRET missing in .env")


def main() -> int:
    # Sample SPY price — bridge uses offsets from payload
    body = {
        "webhookSecret": load_secret(),
        "ticker": "SPY",
        "action": "PUT_CREDIT_SPREAD",
        "signalPrice": 590.0,
        "dteFilter": "weekly",
        "strikeOffsetShort": -5,
        "strikeOffsetLong": -6,
        "quantity": 1,
        "limitCredit": 0.55,
    }
    print("POST", URL)
    print("Payload (secret hidden):", json.dumps({**body, "webhookSecret": "***"}, indent=2))
    r = httpx.post(URL, json=body, timeout=90.0)
    print("HTTP", r.status_code)
    try:
        data = r.json()
        print(json.dumps(data, indent=2)[:2000])
        ok = bool(data.get("success"))
        if data.get("dry_run"):
            print("NOTE: dry_run=True — no order sent (execution mode sandbox?)")
        return 0 if ok else 1
    except Exception:
        print(r.text[:500])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())