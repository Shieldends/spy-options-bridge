"""Alpaca paper: open/close put credit spreads as single-leg orders (mleg won't fill)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

PAPER_SELL_LIMIT = 0.05
PAPER_BUY_LONG_LIMIT = 0.12
PAPER_CLOSE_CHASE_STEPS = 6
PAPER_CLOSE_CHASE_STEP = 0.02


def should_use_paper_spread_legs(settings: Any, spread: Any) -> bool:
    """Alpaca paper never fills mleg — any 2-leg spread uses single-leg orders."""
    return bool(
        settings.use_alpaca
        and settings.is_alpaca_paper
        and not spread.metadata.get("single_leg")
        and len(getattr(spread, "legs", []) or []) >= 2
    )


def _headers(settings: Any) -> dict[str, str]:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "Apca-Api-Key-Id": settings.alpaca_key,
        "Apca-Api-Secret-Key": settings.alpaca_secret,
    }


def _base(settings: Any) -> str:
    return settings.apca_api_base_url.rstrip("/")


async def _post_order(settings: Any, payload: dict[str, Any]) -> tuple[bool, dict[str, Any], str]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{_base(settings)}/v2/orders", headers=_headers(settings), json=payload)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:300]}
    if r.is_success:
        return True, body, "ok"
    return False, body, f"Alpaca rejected ({r.status_code})"


async def _delete_position(settings: Any, symbol: str) -> tuple[bool, int]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.delete(f"{_base(settings)}/v2/positions/{symbol}", headers=_headers(settings))
    return r.is_success, r.status_code


async def _cancel_order(settings: Any, order_id: str) -> None:
    async with httpx.AsyncClient(timeout=20.0) as client:
        await client.delete(f"{_base(settings)}/v2/orders/{order_id}", headers=_headers(settings))


async def wait_order_filled(
    settings: Any,
    order_id: str,
    *,
    max_wait_sec: float = 28.0,
    poll_sec: float = 1.5,
) -> dict[str, Any] | None:
    deadline = asyncio.get_event_loop().time() + max_wait_sec
    terminal = {"canceled", "expired", "rejected", "failed", "done_for_day"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        while asyncio.get_event_loop().time() < deadline:
            r = await client.get(f"{_base(settings)}/v2/orders/{order_id}", headers=_headers(settings))
            if not r.is_success:
                await asyncio.sleep(poll_sec)
                continue
            order = r.json()
            status = str(order.get("status", "")).lower()
            if status == "filled" or float(order.get("filled_qty") or 0) > 0:
                return order
            if status in terminal:
                return None
            await asyncio.sleep(poll_sec)
    return None


async def submit_paper_spread_entry(settings: Any, spread: Any, *, dry_run: bool) -> tuple[bool, str, dict[str, Any]]:
    """Sell short put then buy long put — both single-leg limits tuned for paper fills."""
    if dry_run:
        return True, "Paper spread legs packaged (dry-run)", {"dry_run": True}

    short_sym = spread.legs[0].symbol
    long_sym = spread.legs[1].symbol
    qty = str(spread.qty)

    short_payload = {
        "symbol": short_sym,
        "qty": qty,
        "side": "sell",
        "type": "limit",
        "limit_price": f"{PAPER_SELL_LIMIT:.2f}",
        "time_in_force": "day",
    }
    ok, body, msg = await _post_order(settings, short_payload)
    if not ok:
        return False, f"Short leg rejected: {msg}", body

    short_id = str(body.get("id", ""))
    filled_short = await wait_order_filled(settings, short_id)
    if not filled_short:
        await _cancel_order(settings, short_id)
        market_short = {**short_payload, "type": "market"}
        market_short.pop("limit_price", None)
        ok_ms, body_ms, msg_ms = await _post_order(settings, market_short)
        if not ok_ms:
            return False, f"Short leg limit+market failed: {msg_ms}", {"short_order_id": short_id}
        short_id = str(body_ms.get("id", ""))
        filled_short = await wait_order_filled(settings, short_id, max_wait_sec=35.0)
        if not filled_short:
            await _cancel_order(settings, short_id)
            return False, "Short leg did not fill on Alpaca paper (limit or market)", {"short_order_id": short_id}

    long_payload = {
        "symbol": long_sym,
        "qty": qty,
        "side": "buy",
        "type": "limit",
        "limit_price": f"{PAPER_BUY_LONG_LIMIT:.2f}",
        "time_in_force": "day",
    }
    ok2, body2, msg2 = await _post_order(settings, long_payload)
    if not ok2:
        await _delete_position(settings, short_sym)
        return False, f"Long leg rejected after short filled — unwound short: {msg2}", body2

    long_id = str(body2.get("id", ""))
    filled_long = await wait_order_filled(settings, long_id)
    if not filled_long:
        await _cancel_order(settings, long_id)
        await _delete_position(settings, short_sym)
        return False, "Long leg did not fill — unwound short leg", {"long_order_id": long_id}

    net_credit = round(PAPER_SELL_LIMIT - PAPER_BUY_LONG_LIMIT, 2)
    return True, f"Paper spread legs filled (net ~${net_credit:.2f})", {
        "paper_spread_legs": True,
        "short_order_id": short_id,
        "long_order_id": long_id,
        "net_credit_estimate": net_credit,
    }


async def _close_leg_with_chase(
    settings: Any,
    *,
    symbol: str,
    qty: int,
    side: str,
    start_limit: float,
    dry_run: bool,
) -> bool:
    if dry_run:
        return True
    limit = round(start_limit, 2)
    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": "limit",
        "limit_price": f"{limit:.2f}",
        "time_in_force": "day",
    }
    ok, body, _ = await _post_order(settings, payload)
    if not ok:
        ok_del, _ = await _delete_position(settings, symbol)
        return ok_del

    oid = str(body.get("id", ""))
    if await wait_order_filled(settings, oid, max_wait_sec=18.0):
        return True

    await _cancel_order(settings, oid)
    for _ in range(PAPER_CLOSE_CHASE_STEPS):
        limit = round(limit + PAPER_CLOSE_CHASE_STEP, 2)
        payload["limit_price"] = f"{limit:.2f}"
        ok, body, _ = await _post_order(settings, payload)
        if not ok:
            break
        oid = str(body.get("id", ""))
        if await wait_order_filled(settings, oid, max_wait_sec=10.0):
            return True
        await _cancel_order(settings, oid)

    ok_del, _ = await _delete_position(settings, symbol)
    return ok_del


async def close_paper_spread_legs(settings: Any, spread: Any, *, dry_run: bool) -> tuple[bool, str]:
    """Buy back short put, sell long put (with chase, then market flat per leg)."""
    qty = int(spread.qty)
    short_sym = spread.legs[0].symbol
    long_sym = spread.legs[1].symbol

    short_ok = await _close_leg_with_chase(
        settings,
        symbol=short_sym,
        qty=qty,
        side="buy",
        start_limit=0.08,
        dry_run=dry_run,
    )
    long_ok = await _close_leg_with_chase(
        settings,
        symbol=long_sym,
        qty=qty,
        side="sell",
        start_limit=0.03,
        dry_run=dry_run,
    )
    if short_ok and long_ok:
        return True, "Paper spread closed (both legs)"
    if short_ok or long_ok:
        return True, "Paper spread partially closed — check Alpaca positions"
    return False, "Paper spread close failed on both legs"


async def open_crush_it_short_put(
    settings: Any,
    *,
    underlying: str,
    expiration: str,
    strike: float,
    option_type: str,
    quantity: int,
    dry_run: bool,
) -> tuple[bool, str, dict[str, Any]]:
    """Open single short option for Crush-It tickers (STX, etc.) on Alpaca paper."""
    from main import format_occ_symbol  # noqa: WPS433 — runtime import avoids cycle at load

    sym = format_occ_symbol(underlying, expiration, option_type, strike)
    if dry_run:
        return True, f"Would sell {sym} @ ${PAPER_SELL_LIMIT:.2f}", {"symbol": sym, "dry_run": True}

    payload = {
        "symbol": sym,
        "qty": str(quantity),
        "side": "sell",
        "type": "limit",
        "limit_price": f"{PAPER_SELL_LIMIT:.2f}",
        "time_in_force": "day",
    }
    ok, body, msg = await _post_order(settings, payload)
    if not ok:
        return False, msg, body

    oid = str(body.get("id", ""))
    filled = await wait_order_filled(settings, oid)
    if not filled:
        await _cancel_order(settings, oid)
        market_payload = {
            "symbol": sym,
            "qty": str(quantity),
            "side": "sell",
            "type": "market",
            "time_in_force": "day",
        }
        ok_m, body_m, msg_m = await _post_order(settings, market_payload)
        if not ok_m:
            return False, f"Crush-It open limit+market failed: {sym} ({msg_m})", {"order_id": oid}
        oid = str(body_m.get("id", ""))
        filled = await wait_order_filled(settings, oid, max_wait_sec=35.0)
        if not filled:
            await _cancel_order(settings, oid)
            return False, f"Crush-It open did not fill (limit or market): {sym}", {"order_id": oid}
        return True, f"Crush-It short opened {sym} @ market (limit ${PAPER_SELL_LIMIT:.2f} missed)", {
            "order_id": oid,
            "symbol": sym,
            "filled": True,
            "fill_mode": "market_fallback",
        }

    return True, f"Crush-It short opened {sym} @ paper limit ${PAPER_SELL_LIMIT:.2f}", {
        "order_id": oid,
        "symbol": sym,
        "filled": True,
    }
