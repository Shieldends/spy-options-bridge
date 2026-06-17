"""Multi-strike sniper grid tests."""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

from main import (
    Settings,
    discover_sniper_grid_resumes,
    expected_hedge_strike,
    hydrate_traps_for_short_put,
    is_force_fill_phase,
    passive_interval_seconds,
    protective_hedge_telemetry_from_traps,
    record_protective_hedge_telemetry,
    resolve_hedge_put_contract,
    run_sniper_grid_loop,
    sniper_grid_session_key,
)

ET = ZoneInfo("America/New_York")


def test_expected_hedge_strike_offsets():
    assert expected_hedge_strike(970.0, -10.0) == 960.0
    assert expected_hedge_strike(970.0, -15.0) == 955.0


@pytest.mark.asyncio
async def test_hydrate_traps_from_open_orders():
    orders = [
        {
            "id": "a1",
            "side": "buy",
            "symbol": "STX260618P00960000",
            "limit_price": "0.09",
            "order_class": "simple",
        },
        {
            "id": "b1",
            "side": "buy",
            "symbol": "STX260618P00955000",
            "limit_price": "0.05",
            "order_class": "simple",
        },
        {
            "id": "c1",
            "side": "buy",
            "symbol": "STX260618P00950000",
            "limit_price": "0.03",
            "order_class": "simple",
        },
    ]
    traps = await hydrate_traps_for_short_put(
        underlying="STX",
        expiration="2026-06-18",
        short_strike=970.0,
        open_orders=orders,
    )
    assert len(traps) == 3
    assert traps[0]["leg"] == "A"
    assert traps[0]["order_id"] == "a1"
    assert traps[1]["hedge_strike"] == 955.0


@pytest.mark.asyncio
async def test_discover_sniper_grid_resumes_stx_short():
    settings = Settings(STX_SNIPER_GRID_ENABLED=True)
    positions = [{"symbol": "STX260618P00970000", "qty": "-1"}]
    orders = [
        {"id": "a1", "side": "buy", "symbol": "STX260618P00960000", "limit_price": "0.09"},
        {"id": "b1", "side": "buy", "symbol": "STX260618P00955000", "limit_price": "0.05"},
    ]
    with (
        patch("main.fetch_alpaca_positions", AsyncMock(return_value=positions)),
        patch("main.fetch_alpaca_open_orders", AsyncMock(return_value=orders)),
    ):
        sessions = await discover_sniper_grid_resumes(settings)
    assert len(sessions) == 1
    assert sessions[0]["short_strike"] == 970.0
    assert len(sessions[0]["traps"]) == 2
    assert sniper_grid_session_key("STX", "2026-06-18", 970.0) in sessions[0]["signal_id"]


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


def test_protective_hedge_telemetry_schema():
    traps = [
        {"leg": "A", "order_id": "oid-a", "hedge_strike": 975.0},
        {"leg": "B", "order_id": "oid-b", "hedge_strike": 970.0},
        {"leg": "C", "order_id": "oid-c", "hedge_strike": 965.0},
    ]
    ph = protective_hedge_telemetry_from_traps(traps)
    assert ph["leg_a"] == {"order_id": "oid-a", "strike": 975.0, "status": "pending"}
    assert ph["leg_b"] == {"order_id": "oid-b", "strike": 970.0, "status": "pending"}
    assert ph["leg_c"] == {"order_id": "oid-c", "strike": 965.0, "status": "pending"}


def test_protective_hedge_telemetry_failed_leg():
    traps = [{"leg": "A", "order_id": "oid-a", "hedge_strike": 975.0}]
    ph = protective_hedge_telemetry_from_traps(traps)
    assert ph["leg_a"]["status"] == "pending"
    assert ph["leg_b"]["status"] == "failed"
    assert ph["leg_c"]["status"] == "failed"


def test_record_protective_hedge_telemetry_writes_jsonl(tmp_path):
    log_path = tmp_path / "activity.jsonl"
    traps = [
        {"leg": "A", "order_id": "a1", "hedge_strike": 975.0},
        {"leg": "B", "order_id": "b1", "hedge_strike": 970.0},
        {"leg": "C", "order_id": "c1", "hedge_strike": 965.0},
    ]
    with patch("main._ACTIVITY_LOG_PATH", log_path):
        sid = record_protective_hedge_telemetry(
            traps,
            underlying="STX",
            expiration="2026-06-18",
            short_strike=985.0,
            signal_id="STX|2026-06-18|985.00|test",
        )
    assert sid == "STX|2026-06-18|985.00|test"
    row = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert row["signal_id"] == sid
    assert row["protective_hedge"]["leg_a"]["order_id"] == "a1"
    assert row["protective_hedge"]["leg_b"]["strike"] == 970.0
    assert row["protective_hedge"]["leg_c"]["status"] == "pending"
    assert row["timestamp"].endswith("-04:00") or row["timestamp"].endswith("-05:00")


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
