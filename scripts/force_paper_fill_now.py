#!/usr/bin/env python3
"""Fire one paper entry via Render /exercise/entry (sync chase) and poll Alpaca."""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
RENDER = "https://spy-options-bridge.onrender.com"
RESULT_PATH = Path(r"C:\Users\Shiel\Desktop\PAPER-FILL-TEST-RESULT.txt")
ET = ZoneInfo("America/New_York")


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


def write_result(lines: list[str]) -> None:
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    body = "\n".join([f"PAPER FILL TEST | {ts}", ""] + lines) + "\n"
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(body, encoding="utf-8")
    print(body)


def main() -> int:
    env = load_env()
    secret = env.get("WEBHOOK_SECRET", "")
    key = env.get("APCA_API_KEY_ID") or env.get("ALPACA_API_KEY", "")
    sec = env.get("APCA_API_SECRET_KEY") or env.get("ALPACA_SECRET_KEY", "")
    base = env.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")

    if not secret:
        write_result(["FAIL: WEBHOOK_SECRET missing in .env"])
        return 1

    signal_price = 590.0
    if key and sec:
        h = {"Apca-Api-Key-Id": key, "Apca-Api-Secret-Key": sec}
        for url in (
            f"{base}/v2/stocks/SPY/quotes/latest",
            "https://data.alpaca.markets/v2/stocks/SPY/trades/latest",
        ):
            try:
                r = httpx.get(url, headers=h, timeout=20)
                if not r.is_success:
                    continue
                j = r.json()
                p = j.get("quote", {}).get("ap") or j.get("trade", {}).get("p")
                if p:
                    signal_price = float(p)
                    break
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
    }
    print(f"POST {RENDER}/exercise/entry signalPrice={signal_price:.2f}")
    try:
        r = httpx.post(f"{RENDER}/exercise/entry", json=body, timeout=180)
        data = r.json()
    except Exception as exc:
        write_result([f"FAIL: HTTP {type(exc).__name__}: {exc}"])
        return 1

    filled = bool(data.get("filled"))
    status = data.get("status", "?")
    oid = str(data.get("order_id") or "")[:12]
    msg = data.get("message", "")

    lines = [
        f"HTTP {r.status_code}",
        f"filled={'YES' if filled else 'NO'}",
        f"status={status}",
        f"order_id={oid}",
        f"message={msg}",
        "",
        "Check Alpaca APP → Activities (not Orders tab alone).",
    ]
    if not filled:
        lines.append("Tip: run during RTH 9:30-16 ET; check open_mleg_count on /health.")
    write_result(lines)
    return 0 if filled else 1


if __name__ == "__main__":
    raise SystemExit(main())
