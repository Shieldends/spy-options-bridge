import pytest

from app.config import Settings
from app.models import SpreadStrategy, TradingViewSignal
from app.spread_builder import build_credit_spread_order, coerce_signal, format_occ_symbol


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        DEFAULT_LIMIT_CREDIT=0.50,
        DEFAULT_STRIKE_OFFSET_SHORT=-2,
        DEFAULT_STRIKE_OFFSET_LONG=-3,
    )


def test_occ_symbol_format():
    assert format_occ_symbol("SPY", "2026-06-03", "put", 579) == "SPY260603P00579000"


def test_put_credit_spread_package(settings):
    signal = TradingViewSignal.model_validate(
        {
            "ticker": "SPY",
            "strategy": "put_credit_spread",
            "signalPrice": 581.73,
            "strikeOffsetShort": -2,
            "strikeOffsetLong": -3,
            "limitCredit": 0.45,
            "expiration": "2026-06-03",
        }
    )
    order = build_credit_spread_order(signal, settings)

    assert order.order_class == "mleg"
    assert order.limit_price == "-0.45"
    assert len(order.legs) == 2
    assert order.legs[0].position_intent == "sell_to_open"
    assert order.legs[1].position_intent == "buy_to_open"
    assert order.metadata["short_strike"] == 579.0
    assert order.metadata["long_strike"] == 578.0


def test_call_credit_spread_offsets(settings):
    signal = TradingViewSignal.model_validate(
        {
            "ticker": "SPY",
            "strategy": "call_credit_spread",
            "signalPrice": 580.0,
            "strikeOffsetShort": 2,
            "strikeOffsetLong": 3,
            "expiration": "2026-06-03",
        }
    )
    order = build_credit_spread_order(signal, settings)
    assert order.metadata["short_strike"] == 582.0
    assert order.metadata["long_strike"] == 583.0


def test_coerce_signal_applies_defaults(settings):
    signal = coerce_signal({"signalPrice": 500, "strategy": "put_credit_spread"}, settings)
    assert signal.ticker == "SPY"
    assert signal.quantity == 1
