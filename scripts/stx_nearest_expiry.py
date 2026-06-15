#!/usr/bin/env python3
"""Print nearest live STX put expiry for Crush It TV JSON."""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    p = ROOT / ".env"
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> int:
    env = load_env()
    h = {
        "Apca-Api-Key-Id": env.get("APCA_API_KEY_ID", ""),
        "Apca-Api-Secret-Key": env.get("APCA_API_SECRET_KEY", ""),
    }
    if not h["Apca-Api-Key-Id"]:
        print("FAIL: missing APCA keys in .env")
        return 1
    today = date.today()
    url = "https://data.alpaca.markets/v1beta1/options/contracts"
    params = {
        "underlying_symbols": "STX",
        "status": "active",
        "type": "put",
        "limit": 200,
    }
    r = httpx.get(url, headers=h, params=params, timeout=30)
    if not r.is_success:
        print(f"FAIL: contracts HTTP {r.status_code}")
        return 1
    rows = r.json().get("option_contracts") or r.json().get("contracts") or []
    expiries: set[date] = set()
    for c in rows:
        exp_raw = c.get("expiration_date") or c.get("expiration")
        if not exp_raw:
            continue
        exp = datetime.strptime(str(exp_raw)[:10], "%Y-%m-%d").date()
        if exp >= today:
            expiries.add(exp)
    if not expiries:
        print("FAIL: no live STX put expiries found")
        return 1
    nearest = min(expiries)
    print(f"STX nearest live put expiry: {nearest.isoformat()}")
    print("Update BOTH TV alerts (entry + close) expiration field to this date.")
    strikes = sorted(
        {
            float(c.get("strike_price") or c.get("strike") or 0)
            for c in rows
            if str(c.get("expiration_date") or c.get("expiration") or "")[:10] == nearest.isoformat()
        }
    )
    if strikes:
        mid = strikes[len(strikes) // 2]
        print(f"Sample strikes on that expiry: {strikes[:8]} ... (mid ~ {mid})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
