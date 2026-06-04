#!/usr/bin/env python3
"""Test /warning endpoint (override=true avoids closing positions)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = "https://spy-options-bridge.onrender.com/warning"


def load_secret() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("WEBHOOK_SECRET="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("WEBHOOK_SECRET missing")


def main() -> int:
    body = {
        "webhookSecret": load_secret(),
        "ticker": "SPY",
        "signalPrice": 758.0,
        "strikeOffsetShort": -5,
        "strikeOffsetLong": -6,
        "overrideAutoClose": True,
    }
    r = httpx.post(URL, json=body, timeout=90.0)
    print("HTTP", r.status_code)
    data = r.json()
    print(json.dumps({k: data.get(k) for k in (
        "danger_zone", "action_taken", "survival_odds_expire_otm", "risk_warning"
    )}, indent=2))
    return 0 if r.is_success else 1


if __name__ == "__main__":
    raise SystemExit(main())