#!/usr/bin/env python3
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
secret = ""
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if line.strip().startswith("WEBHOOK_SECRET="):
        secret = line.split("=", 1)[1].strip().strip('"').strip("'")

body = {
    "webhookSecret": secret,
    "ticker": "SPY",
    "action": "PUT_CREDIT_SPREAD",
    "signalPrice": 759.89,
    "dteFilter": "weekly",
    "strikeOffsetShort": -5,
    "strikeOffsetLong": -6,
    "quantity": 1,
    "fillMode": "aggressive",
    "limitCredit": 0.55,
}
r = httpx.post("https://spy-options-bridge.onrender.com/entry", json=body, timeout=90)
data = r.json()
print("HTTP", r.status_code, "success", data.get("success"), "msg", data.get("message"))
entry = data.get("entry") or {}
print("limit", (entry.get("payload") or {}).get("limit_price"))
print("order_id", (entry.get("broker_response") or {}).get("id"))
print("metadata", json.dumps((entry.get("payload") or {}), indent=2)[:500])