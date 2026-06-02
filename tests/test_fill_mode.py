from __future__ import annotations

import pytest

from main import SpreadLeg, SpreadPackage, estimate_credit_from_quotes, get_settings


@pytest.fixture(autouse=True)
def clear_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_aggressive_credit_from_bid_ask():
    spread = SpreadPackage(
        qty="1",
        legs=[
            SpreadLeg(symbol="SPY260605P00752000", side="sell", position_intent="sell_to_open"),
            SpreadLeg(symbol="SPY260605P00751000", side="buy", position_intent="buy_to_open"),
        ],
        metadata={},
    )
    quotes = {
        "SPY260605P00752000": {"bid": 0.80, "ask": 0.85},
        "SPY260605P00751000": {"bid": 0.20, "ask": 0.25},
    }
    credit, meta = estimate_credit_from_quotes(spread, quotes, mode="aggressive", cap=0.55)
    assert credit < 0.55
    assert credit >= 0.05
    assert meta["quote_source"] == "bid_ask_aggressive"


def test_cap_limits_high_fixed():
    spread = SpreadPackage(
        qty="1",
        legs=[
            SpreadLeg(symbol="SPY260605P00752000", side="sell", position_intent="sell_to_open"),
            SpreadLeg(symbol="SPY260605P00751000", side="buy", position_intent="buy_to_open"),
        ],
        metadata={},
    )
    quotes = {
        "SPY260605P00752000": {"bid": 2.0, "ask": 2.1},
        "SPY260605P00751000": {"bid": 0.5, "ask": 0.6},
    }
    credit, _ = estimate_credit_from_quotes(spread, quotes, mode="auto", cap=0.55)
    assert credit == 0.55