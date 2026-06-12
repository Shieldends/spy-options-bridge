"""Unit tests for Alpaca-specific main.py helpers."""
from __future__ import annotations

import pytest

from datetime import datetime
from zoneinfo import ZoneInfo

from main import (
    build_alpaca_entry_payload,
    build_alpaca_single_leg_payload,
    build_order_from_signal,
    build_spread,
    coerce_signal,
    conservative_close_limit,
    format_alpaca_limit_price,
    get_settings,
    resolve_dte_expiration,
    snap_put_credit_strikes,
    split_batches,
    SpreadLeg,
    SpreadPackage,
    webhook_auth_error,
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


def test_webhook_auth_error_messages():
    assert webhook_auth_error("secret", "secret") is None
    assert webhook_auth_error(None, "secret") is not None
    assert "Missing webhookSecret" in webhook_auth_error(None, "secret")
    assert webhook_auth_error("wrong", "secret") is not None


def test_short_put_single_leg_entry_payload():
    settings = get_settings()
    signal = coerce_signal(
        {
            "ticker": "STX",
            "action": "SHORT_PUT",
            "short_strike": 230,
            "dteFilter": "2026-05-15",
            "quantity": 6,
            "limitCredit": 0.21,
        },
        settings,
    )
    spread = build_order_from_signal(signal, settings)
    assert spread.metadata["single_leg"] is True
    assert len(spread.legs) == 1
    payload = build_alpaca_entry_payload(spread)
    assert payload["side"] == "sell"
    assert payload["symbol"].startswith("STX")
    assert payload["limit_price"] == "0.21"


def test_conservative_close_limit_above_bid():
    assert conservative_close_limit(0.08, 0.03) == 0.11
    assert split_batches(24, 6) == [6, 6, 6, 6]


def test_build_alpaca_single_leg_buy_to_close():
    spread = SpreadPackage(
        qty="6",
        legs=[SpreadLeg(symbol="STX260515P00230000", side="sell", position_intent="sell_to_open")],
        metadata={"single_leg": True},
    )
    payload = build_alpaca_single_leg_payload(spread, limit_price=0.11, opening=False)
    assert payload["side"] == "buy"
    assert payload["limit_price"] == "0.11"
    assert payload["qty"] == "6"


def test_resolve_dte_plus_one_and_two():
    et = ZoneInfo("America/New_York")
    now = datetime(2026, 6, 12, 10, 0, tzinfo=et)
    assert resolve_dte_expiration("0dte", None, now=now) == "2026-06-12"
    assert resolve_dte_expiration("+1dte", None, now=now) == "2026-06-13"
    assert resolve_dte_expiration("2dte", None, now=now) == "2026-06-13"
    assert resolve_dte_expiration("+2dte", None, now=now) == "2026-06-14"
