import pytest

from spy_options_bridge.models.signal import TradingViewSignal
from spy_options_bridge.resolvers.strike_resolver import (
    format_osi_symbol,
    resolve_atm_strike,
    resolve_put_credit_strikes,
)


def test_resolve_atm_strike():
    assert resolve_atm_strike(581.73) == 581.0


def test_put_credit_spread_strikes():
    signal = TradingViewSignal.model_validate(
        {
            "ticker": "SPY",
            "action": "buy",
            "signalPrice": 581.73,
            "optionContract": {
                "strikeOffsetShort": -2,
                "strikeOffsetLong": -3,
            },
        }
    )
    strikes = resolve_put_credit_strikes(signal, 581.73)
    assert strikes.short_strike == 579.0
    assert strikes.long_strike == 578.0


def test_osi_symbol_format():
    symbol = format_osi_symbol("SPY", "2026-05-31", "put", 579)
    assert symbol == "SPY 260531P579"


def test_invalid_spread_offsets_raise():
    signal = TradingViewSignal.model_validate(
        {
            "ticker": "SPY",
            "action": "buy",
            "signalPrice": 500.0,
            "optionContract": {"strikeOffsetShort": -3, "strikeOffsetLong": -2},
        }
    )
    with pytest.raises(ValueError, match="short strike"):
        resolve_put_credit_strikes(signal, 500.0)
