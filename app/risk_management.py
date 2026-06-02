"""Take-profit exits and short-strike danger zone checks."""

from __future__ import annotations

import logging

from app.models import MLegOrderPackage
from app.spread_builder import compact_occ_to_tastytrade_symbol

logger = logging.getLogger(__name__)


def check_danger_zone(
    underlying_price: float,
    short_strike: float,
    *,
    danger_pct: float = 0.01,
    ticker: str = "",
) -> tuple[bool, str]:
    """
    Returns (is_danger, message) when underlying is within danger_pct of short strike.

    For put credit spreads, danger = price falling toward short put strike.
    For call credit spreads, danger = price rising toward short call strike.
    """
    if short_strike <= 0:
        return False, ""

    distance_pct = abs(underlying_price - short_strike) / short_strike
    if distance_pct <= danger_pct:
        msg = (
            f"CRITICAL RISK: {ticker or 'Underlying'} ${underlying_price:.2f} is "
            f"{distance_pct * 100:.2f}% from short strike ${short_strike:.2f} "
            f"(threshold {danger_pct * 100:.1f}%)"
        )
        logger.critical(msg)
        return True, msg

    return False, ""


def build_take_profit_close_payload(
    entry: MLegOrderPackage,
    *,
    take_profit_pct: float = 0.50,
) -> dict:
    """
    Build a multi-leg 'Buy to Close' limit order at 50% of max premium collected.

    Example: $0.50 credit entry → close at $0.25 debit (locks ~50% of max profit).
    """
    credit = float(entry.metadata.get("limit_credit", 0.50))
    close_debit = round(credit * take_profit_pct, 2)
    qty = int(entry.qty)

    short_symbol = compact_occ_to_tastytrade_symbol(entry.legs[0].symbol)
    long_symbol = compact_occ_to_tastytrade_symbol(entry.legs[1].symbol)

    return {
        "time-in-force": "GTC",
        "order-type": "Limit",
        "price": f"{close_debit:.2f}",
        "price-effect": "Debit",
        "legs": [
            {
                "instrument-type": "Equity Option",
                "symbol": short_symbol,
                "quantity": qty,
                "action": "Buy to Close",
            },
            {
                "instrument-type": "Equity Option",
                "symbol": long_symbol,
                "quantity": qty,
                "action": "Sell to Close",
            },
        ],
        "_meta": {
            "entry_credit": credit,
            "take_profit_pct": take_profit_pct,
            "close_debit": close_debit,
            "profit_locked": round(credit - close_debit, 2),
        },
    }
