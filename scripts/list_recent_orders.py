#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
env: dict[str, str] = {}
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

headers = {
    "Apca-Api-Key-Id": env["APCA_API_KEY_ID"],
    "Apca-Api-Secret-Key": env["APCA_API_SECRET_KEY"],
}
base = env.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")
r = httpx.get(f"{base}/v2/orders", headers=headers, params={"status": "all", "limit": 20, "nested": "true"}, timeout=30)
print("HTTP", r.status_code)
for o in r.json():
    if o.get("order_class") != "mleg":
        continue
    ts = (o.get("created_at") or "")[:19]
    print(ts, o.get("status"), "limit", o.get("limit_price"), "id", (o.get("id") or "")[:8])