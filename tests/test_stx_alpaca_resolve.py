"""STX open resolves to real Alpaca contracts (not stale TV strike)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from main import StxCloseSignal, get_settings, resolve_stx_open_contract


@pytest.fixture
def settings():
    get_settings.cache_clear()
    return get_settings()


@pytest.mark.asyncio
async def test_stale_strike_ignored_uses_otm(settings):
    signal = StxCloseSignal(
        underlying="STX",
        strike=230.0,
        dteFilter="weekly",
        type="put",
        mode="open",
        confirmOpen=True,
    )
    with (
        patch("main.fetch_alpaca_nearest_option_expiry", AsyncMock(return_value="2026-06-18")),
        patch("main.fetch_alpaca_option_strikes", AsyncMock(return_value=[1030.0, 1035.0, 1040.0, 1045.0])),
        patch("main.fetch_alpaca_underlying_price", AsyncMock(return_value=1043.95)),
        patch("main.fetch_alpaca_options_buying_power", AsyncMock(return_value=200_000.0)),
        patch("main.verify_alpaca_option_contract", AsyncMock(return_value=True)),
    ):
        out = await resolve_stx_open_contract(settings, signal)
    assert out["expiration"] == "2026-06-18"
    assert out["strike"] == 1030.0  # ~10 below 1044, snapped to listed put
    assert out["meta"]["strike_source"] == "json_strike_stale_ignored"


@pytest.mark.asyncio
async def test_strike_offset_from_signal_price(settings):
    signal = StxCloseSignal(
        underlying="STX",
        strikeOffset=-10.0,
        signalPrice=1040.0,
        dteFilter="weekly",
        type="put",
        mode="open",
        confirmOpen=True,
    )
    with (
        patch("main.fetch_alpaca_nearest_option_expiry", AsyncMock(return_value="2026-06-18")),
        patch("main.fetch_alpaca_option_strikes", AsyncMock(return_value=[1020.0, 1030.0, 1040.0])),
        patch("main.fetch_alpaca_options_buying_power", AsyncMock(return_value=200_000.0)),
        patch("main.verify_alpaca_option_contract", AsyncMock(return_value=True)),
    ):
        out = await resolve_stx_open_contract(settings, signal)
    assert out["strike"] == 1030.0
    assert out["meta"]["strike_source"] == "strikeOffset"


def test_stx_signal_accepts_strike_offset_alias():
    sig = StxCloseSignal.model_validate(
        {"underlying": "STX", "strikeOffset": -5, "signalPrice": 100.0, "mode": "open", "confirmOpen": True}
    )
    assert sig.strike_offset == -5.0
    assert sig.signal_price == 100.0


def test_cap_put_strike_for_buying_power():
    from main import cap_put_strike_for_buying_power

    available = [960.0, 970.0, 980.0, 990.0, 1000.0, 1030.0]
    strike, capped = cap_put_strike_for_buying_power(1030.0, available, 99350.0, 1)
    assert capped is True
    assert strike == 980.0
