"""Unit tests for Alpaca-specific main.py helpers."""
from __future__ import annotations

import pytest

from main import (
    build_alpaca_entry_payload,
    build_spread,
    coerce_signal,
    format_alpaca_limit_price,
    get_settings,
    snap_put_credit_strikes,
    SpreadLeg,
    SpreadPackage,
)


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_alpaca_credit_limit_price_is_negative():
    assert format_alpaca_limit_price(0.35, is_credit=True) == "-0.35"


def test_alpaca_debit_limit_price_is_positive():
    assert format_alpaca_limit_price(0.18, is_credit=False) == "0.18"


def test_snap_put_credit_strikes_to_alpaca_chain():
    available = [570.0, 575.0, 580.0, 585.0, 590.0]
    short, long = snap_put_credit_strikes(585.0, 582.0, available)
    assert short == 585.0
    assert long == 580.0


def test_build_alpaca_entry_payload_uses_negative_credit():
    settings = get_settings()
    signal = coerce_signal(
        {
            "ticker": "SPY",
            "signalPrice": 590,
            "action": "PUT_CREDIT_SPREAD",
            "dteFilter": "weekly",
            "strikeOffsetShort": -5,
            "strikeOffsetLong": -8,
            "limitCredit": 0.35,
        },
        settings,
    )
    spread = build_spread(signal, settings)
    payload = build_alpaca_entry_payload(spread)
    assert payload["order_class"] == "mleg"
    assert payload["limit_price"] == "-0.35"
    assert payload["legs"][0]["position_intent"] == "sell_to_open"


def test_build_alpaca_close_payload_uses_positive_debit():
    from main import build_alpaca_close_payload

    spread = SpreadPackage(
        qty="1",
        legs=[
            SpreadLeg(symbol="SPY260605P00585000", side="sell", position_intent="sell_to_open"),
            SpreadLeg(symbol="SPY260605P00580000", side="buy", position_intent="buy_to_open"),
        ],
        metadata={"limit_credit": 0.35},
    )
    payload = build_alpaca_close_payload(spread, 0.18)
    assert payload["limit_price"] == "0.18"
    assert payload["legs"][0]["position_intent"] == "buy_to_close"
