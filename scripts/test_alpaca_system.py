"""End-to-end Alpaca paper system check for spy-options-bridge."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from main import (  # noqa: E402
    build_alpaca_entry_payload,
    build_spread,
    build_take_profit_payload,
    build_stop_loss_payload,
    coerce_signal,
    get_settings,
    submit_alpaca_order,
)

ET = ZoneInfo("America/New_York")


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


async def check_alpaca_account(settings) -> dict:
    base = settings.apca_api_base_url.rstrip("/")
    headers = {
        "Apca-Api-Key-Id": settings.alpaca_key,
        "Apca-Api-Secret-Key": settings.alpaca_secret,
    }
    results = {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{base}/v2/account", headers=headers)
        results["account_status"] = r.status_code
        results["account"] = r.json() if r.is_success else {"error": r.text}
        r2 = await client.get(f"{base}/v2/account/configurations", headers=headers)
        results["config_status"] = r2.status_code
        results["config"] = r2.json() if r2.is_success else {"error": r2.text}
    return results


async def check_option_contracts(settings, symbols: list[str]) -> dict:
    base = settings.apca_api_base_url.rstrip("/")
    headers = {
        "Apca-Api-Key-Id": settings.alpaca_key,
        "Apca-Api-Secret-Key": settings.alpaca_secret,
    }
    out = {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for sym in symbols:
            r = await client.get(
                f"{base}/v2/options/contracts/{sym}",
                headers=headers,
            )
            out[sym] = {
                "status": r.status_code,
                "body": r.json() if r.is_success else r.text[:500],
            }
    return out


async def test_entry_order(settings, spread, *, submit: bool) -> dict:
    payload = build_alpaca_entry_payload(spread)
    print("Entry payload:", json.dumps(payload, indent=2))
    if not submit:
        return {"skipped": True, "payload": payload}
    result = await submit_alpaca_order(settings, payload, dry_run=False)
    return {
        "success": result.success,
        "message": result.message,
        "broker_response": result.broker_response,
        "payload": payload,
    }


async def main() -> int:
    get_settings.cache_clear()
    settings = get_settings()
    issues: list[str] = []

    section("1. Settings")
    print(f"  broker={settings.broker}")
    print(f"  use_alpaca={settings.use_alpaca}")
    print(f"  configured={settings.configured}")
    print(f"  execution_mode={settings.execution_mode}")
    print(f"  api={settings.apca_api_base_url}")
    if not settings.use_alpaca:
        issues.append("BROKER is not alpaca — set BROKER=alpaca")
    if not settings.alpaca_configured:
        issues.append("Alpaca keys missing in .env")

    section("2. Alpaca account auth")
    acct = await check_alpaca_account(settings)
    print(json.dumps(acct, indent=2, default=str))
    if acct["account_status"] != 200:
        issues.append(f"Alpaca auth failed: HTTP {acct['account_status']}")
    else:
        acc = acct["account"]
        print(f"  status={acc.get('status')} options_buying_power={acc.get('options_buying_power')}")
        cfg = acct.get("config", {})
        if isinstance(cfg, dict):
            print(f"  max_options_trading_level={cfg.get('max_options_trading_level')}")
            lvl = cfg.get("max_options_trading_level", 0)
            if lvl is not None and int(lvl) < 3:
                issues.append(f"Options level {lvl} — need Level 3 for spreads (enable in Alpaca dashboard)")

    section("3. Build spread from TradingView-style payload")
    signal_payload = {
        "ticker": "SPY",
        "action": "PUT_CREDIT_SPREAD",
        "signalPrice": 590.0,
        "dteFilter": "weekly",
        "strikeOffsetShort": -5,
        "strikeOffsetLong": -8,
        "quantity": 1,
        "limitCredit": 0.35,
    }
    signal = coerce_signal(signal_payload, settings)
    spread = build_spread(signal, settings)
    print(f"  expiration={spread.metadata['expiration']}")
    print(f"  short={spread.metadata['short_strike']} long={spread.metadata['long_strike']}")
    print(f"  symbols: {spread.legs[0].symbol} / {spread.legs[1].symbol}")

    section("4. Verify option contracts exist on Alpaca")
    symbols = [spread.legs[0].symbol, spread.legs[1].symbol]
    contracts = await check_option_contracts(settings, symbols)
    print(json.dumps(contracts, indent=2))
    for sym, info in contracts.items():
        if info["status"] != 200:
            issues.append(f"Option contract not found on Alpaca: {sym} (HTTP {info['status']})")

    section("5. Submit entry order to Alpaca paper")
    now_et = datetime.now(tz=ET)
    print(f"  ET now: {now_et.strftime('%Y-%m-%d %H:%M %Z')} (weekday={now_et.weekday()})")
    entry_result = await test_entry_order(settings, spread, submit=True)
    print(json.dumps(entry_result, indent=2, default=str))
    if not entry_result.get("success"):
        br = entry_result.get("broker_response") or {}
        msg = br.get("message") if isinstance(br, dict) else str(br)
        issues.append(f"Entry order rejected: {entry_result.get('message')} — {msg}")

    section("6. Take-profit / stop-loss payloads (no submit)")
    tp = build_take_profit_payload(spread, settings.take_profit_pct, settings)
    sl = build_stop_loss_payload(spread, settings.stop_loss_multiplier, settings)
    print("TP:", json.dumps({k: v for k, v in tp.items() if not k.startswith("_")}, indent=2))
    print("SL:", json.dumps({k: v for k, v in sl.items() if not k.startswith("_")}, indent=2))

    section("SUMMARY")
    if issues:
        print("ISSUES FOUND:")
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
