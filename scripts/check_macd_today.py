#!/usr/bin/env python3
"""Check if SPY MACD 12,26,9 had entry cross-up today (5-min bars)."""
from __future__ import annotations

from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def ema_series(vals: list[float], period: int) -> list[float]:
    k = 2 / (period + 1)
    e = vals[0]
    out = [e]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def main() -> None:
    env = load_env()
    key = env.get("APCA_API_KEY_ID", "")
    sec = env.get("APCA_API_SECRET_KEY", "")
    headers = {"Apca-Api-Key-Id": key, "Apca-Api-Secret-Key": sec}
    url = "https://data.alpaca.markets/v2/stocks/SPY/bars"
    params = {
        "timeframe": "5Min",
        "start": "2026-06-02T13:30:00Z",
        "limit": 500,
        "adjustment": "raw",
    }
    r = httpx.get(url, headers=headers, params=params, timeout=30.0)
    print("bars HTTP", r.status_code)
    bars = r.json().get("bars") or []
    if not bars:
        print("NO_BARS")
        return

    closes = [float(b["c"]) for b in bars]
    times = [b["t"] for b in bars]
    ema12 = ema_series(closes, 12)
    ema26 = ema_series(closes, 26)
    macd = [a - b for a, b in zip(ema12, ema26)]
    signal = ema_series(macd, 9)

    ups: list[str] = []
    downs: list[str] = []
    for i in range(1, len(macd)):
        if macd[i - 1] <= signal[i - 1] and macd[i] > signal[i]:
            ups.append(times[i])
        if macd[i - 1] >= signal[i - 1] and macd[i] < signal[i]:
            downs.append(times[i])

    print("LAST_BAR", times[-1], "SPY", round(closes[-1], 2))
    print("MACD_NOW", round(macd[-1], 4), "SIGNAL_NOW", round(signal[-1], 4))
    print("ENTRY_CROSS_UP_TODAY", len(ups))
    for t in ups:
        print("  UP", t)
    print("WARNING_CROSS_DOWN_TODAY", len(downs))
    for t in downs[-3:]:
        print("  DOWN", t)


if __name__ == "__main__":
    main()