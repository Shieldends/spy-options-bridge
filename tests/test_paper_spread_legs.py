"""Paper spread legs — Alpaca paper must not use mleg."""

from __future__ import annotations

import pytest

from main import SpreadLeg, SpreadPackage, get_settings
from paper_spread_legs import should_use_paper_spread_legs


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _two_leg_spread() -> SpreadPackage:
    return SpreadPackage(
        qty="1",
        legs=[
            SpreadLeg(symbol="SPY260615P00590000", side="sell", position_intent="sell_to_open"),
            SpreadLeg(symbol="SPY260615P00585000", side="buy", position_intent="buy_to_open"),
        ],
        metadata={"strategy": "put_credit_spread", "limit_credit": 0.45},
    )


def test_should_use_paper_spread_legs_on_alpaca_paper(monkeypatch):
    monkeypatch.setenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("BROKER", "alpaca")
    get_settings.cache_clear()
    settings = get_settings()
    assert should_use_paper_spread_legs(settings, _two_leg_spread()) is True


def test_should_not_use_paper_spread_legs_single_leg(monkeypatch):
    monkeypatch.setenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
    get_settings.cache_clear()
    settings = get_settings()
    spread = SpreadPackage(
        qty="1",
        legs=[SpreadLeg(symbol="STX260718P00230000", side="sell", position_intent="sell_to_open")],
        metadata={"strategy": "short_put", "single_leg": True},
    )
    assert should_use_paper_spread_legs(settings, spread) is False


def test_submit_alpaca_blocks_mleg_on_paper(monkeypatch):
    import asyncio

    monkeypatch.setenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("APCA_API_KEY_ID", "key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "secret")
    monkeypatch.setenv("EXECUTION_MODE", "production")
    get_settings.cache_clear()
    from main import submit_alpaca_order

    settings = get_settings()
    result = asyncio.run(
        submit_alpaca_order(
            settings,
            {"order_class": "mleg", "legs": [], "limit_price": "-0.45"},
            dry_run=False,
        )
    )
    assert result.success is False
    assert "mleg" in result.message.lower()
