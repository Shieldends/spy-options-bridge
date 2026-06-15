"""Pre-entry guards for bull put credit spread strategy (paper stress / risk caps)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

ET = ZoneInfo("America/New_York")


def _alpaca_headers(settings: Any) -> dict[str, str]:
    return {
        "Apca-Api-Key-Id": settings.alpaca_key,
        "Apca-Api-Secret-Key": settings.alpaca_secret,
    }


def _today_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


async def fetch_alpaca_account(settings: Any) -> dict[str, Any]:
    base = settings.apca_api_base_url.rstrip("/")
    headers = _alpaca_headers(settings)
    async with httpx.AsyncClient(timeout=12.0) as client:
        r = await client.get(f"{base}/v2/account", headers=headers)
        r.raise_for_status()
        return r.json()


def _mleg_order_filled(o: dict[str, Any]) -> bool:
    """True only when Alpaca reports a real fill (not resting/canceled 0/1)."""
    if str(o.get("order_class") or "").lower() == "mleg":
        if str(o.get("status") or "").lower() == "filled":
            return True
        try:
            return float(o.get("filled_qty") or 0) > 0
        except (TypeError, ValueError):
            return False
    # Paper spread legs: two simple orders — count short-leg sell fills as spread entry
    if str(o.get("order_class") or "").lower() == "simple" and str(o.get("side") or "").lower() == "sell":
        try:
            return float(o.get("filled_qty") or 0) > 0
        except (TypeError, ValueError):
            return False
    return False


async def count_today_mleg_filled_entries(settings: Any) -> int:
    """Count multi-leg spread orders that actually filled today (ET created_at)."""
    base = settings.apca_api_base_url.rstrip("/")
    headers = _alpaca_headers(settings)
    today = _today_et()
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{base}/v2/orders",
            headers=headers,
            params={"status": "all", "limit": 100, "direction": "desc", "nested": "true"},
        )
        if not r.is_success:
            return 0
        n = 0
        for o in r.json():
            created = str(o.get("created_at") or "")[:10]
            if created != today:
                continue
            if _mleg_order_filled(o):
                n += 1
        return n


async def check_spread_entry_allowed(settings: Any, signal: Any) -> str | None:
    """
    Return skip reason string if entry must not proceed, else None.
    Applies to PUT_CREDIT_SPREAD only.
    """
    if getattr(signal, "is_short_put", False):
        return None
    strat = getattr(signal, "strategy", None)
    if strat is None:
        return None
    strat_val = strat.value if hasattr(strat, "value") else str(strat)
    if strat_val != "put_credit_spread":
        return None

    max_trades = int(getattr(settings, "spread_max_trades_per_day", 0) or 0)
    if max_trades > 0 and settings.alpaca_configured:
        try:
            taken = await count_today_mleg_filled_entries(settings)
            if taken >= max_trades:
                return f"Skip: max filled spreads for today ({taken}/{max_trades})"
        except Exception as exc:
            return f"Skip: could not verify daily trade count ({type(exc).__name__})"

    loss_limit = float(getattr(settings, "spread_daily_loss_limit", 0) or 0)
    if loss_limit > 0 and settings.alpaca_configured:
        try:
            acct = await fetch_alpaca_account(settings)
            equity = float(acct.get("equity") or 0)
            last_eq = float(acct.get("last_equity") or equity)
            day_pnl = equity - last_eq
            if day_pnl <= -loss_limit:
                return f"Skip: daily loss limit (${loss_limit:.0f}) — day P&L ≈ ${day_pnl:.2f}"
        except Exception as exc:
            return f"Skip: could not verify daily P&L ({type(exc).__name__})"

    return None
