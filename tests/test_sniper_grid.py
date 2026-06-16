"""Multi-strike sniper grid tests."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from main import (
    Settings,
    is_force_fill_phase,
    passive_interval_seconds,
    resolve_hedge_put_contract,
    run_sniper_grid_loop,
)

ET = ZoneInfo("America/New_York")


def test_passive_interval_morning():
    morning = datetime(2026, 6, 16, 10, 0, tzinfo=ET)
    assert passive_interval_seconds(morning) == 1200.0


def test_passive_interval_midday():
    midday = datetime(2026, 6, 16, 12, 30, tzinfo=ET)
    assert passive_interval_seconds(midday) == 600.0


def test_force_fill_phase_on_exp_day_after_14():
    afternoon = datetime(2026, 6, 18, 14, 30, tzinfo=ET)
    assert is_force_fill_phase("2026-06-18", afternoon) is True


def test_force_fill_not_on_wrong_day():
    afternoon = datetime(2026, 6, 17, 14, 30, tzinfo=ET)
    assert is_force_fill_phase("2026-06-18", afternoon) is False


@pytest.mark.asyncio
async def test_resolve_three_trap_offsets():
    settings = Settings()
    with (
        patch("main.fetch_alpaca_option_strikes", AsyncMock(return_value=[940.0, 955.0, 965.0, 970.0, 975.0, 980.0, 985.0])),
        patch("main.verify_alpaca_option_contract", AsyncMock(return_value=True)),
    ):
        a = await resolve_hedge_put_contract(settings, underlying="STX", expiration="2026-06-18", short_strike=985.0, hedge_offset=-10.0)
        b = await resolve_hedge_put_contract(settings, underlying="STX", expiration="2026-06-18", short_strike=985.0, hedge_offset=-15.0)
        c = await resolve_hedge_put_contract(settings, underlying="STX", expiration="2026-06-18", short_strike=985.0, hedge_offset=-20.0)
    assert a["hedge_strike"] == 975.0
    assert b["hedge_strike"] == 970.0
    assert c["hedge_strike"] == 965.0


@pytest.mark.asyncio
async def test_grid_submits_three_traps_and_fills_one():
    settings = Settings(STX_SNIPER_GRID_ENABLED=True, EXECUTION_MODE="production", SNIPER_CHASE_SECONDS=0.01)
    order_ids = iter(["a1", "b1", "c1"])

    def make_submit(*_a, **_k):
        oid = next(order_ids)
        return type("R", (), {"success": True, "message": "ok", "broker_response": {"id": oid}})()

    fill_state = {"a1": False, "b1": False, "c1": False}
    poll = {"n": 0}

    async def fake_fetch(_s, oid):
        poll["n"] += 1
        if poll["n"] >= 4:
            fill_state["b1"] = True
        status = "filled" if fill_state.get(oid) else "new"
        fq = "1" if fill_state.get(oid) else "0"
        return {"id": oid, "status": status, "filled_qty": fq, "filled_avg_price": "0.05", "limit_price": "0.04"}

    with (
        patch("main._submit_sniper_trap_leg", AsyncMock(side_effect=[
            {"leg": "A", "order_id": "a1", "symbol": "STX260618P00975000", "limit_price": 0.08, "filled": False},
            {"leg": "B", "order_id": "b1", "symbol": "STX260618P00977000", "limit_price": 0.04, "filled": False},
            {"leg": "C", "order_id": "c1", "symbol": "STX260618P00970000", "limit_price": 0.02, "filled": False},
        ])),
        patch("main.fetch_alpaca_order", fake_fetch),
        patch("main.cancel_alpaca_order", AsyncMock(return_value=True)),
        patch("main.alpaca_market_is_open", AsyncMock(return_value=True)),
        patch("main.replace_alpaca_order_limit_price", AsyncMock(return_value=True)),
        patch("main.record_activity"),
        patch("main.notify", AsyncMock()),
        patch("main.is_force_fill_phase", return_value=False),
        patch("main.passive_interval_seconds", return_value=0.01),
    ):
        await run_sniper_grid_loop(
            settings,
            underlying="STX",
            expiration="2026-06-18",
            short_strike=985.0,
            quantity=1,
        )
