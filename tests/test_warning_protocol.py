from __future__ import annotations

import pytest

from main import (
    estimate_survival_odds_put_credit,
    find_put_credit_spreads_in_positions,
    parse_occ_symbol,
    resolve_warning_close_debit,
    spread_from_put_credit_position,
    SpreadLeg,
    SpreadPackage,
    get_settings,
)


@pytest.fixture(autouse=True)
def clear_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_parse_occ_put():
    meta = parse_occ_symbol("SPY260605P00585000")
    assert meta is not None
    assert meta["underlying"] == "SPY"
    assert meta["strike"] == 585.0
    assert meta["option_type"] == "put"


def test_survival_low_when_below_short():
    survival, notes = estimate_survival_odds_put_credit(580.0, 585.0, 580.0, danger_pct=0.01)
    assert survival < 0.5
    assert any("assignment" in n.lower() or "below" in n.lower() for n in notes)


def test_find_spread_in_positions():
    positions = [
        {"symbol": "SPY260605P00585000", "qty": "-1", "avg_entry_price": "0.30"},
        {"symbol": "SPY260605P00580000", "qty": "1", "avg_entry_price": "-0.05"},
    ]
    spreads = find_put_credit_spreads_in_positions(positions, "SPY", short_strike=585.0, long_strike=580.0)
    assert len(spreads) == 1
    assert spreads[0].legs[0].symbol.endswith("P00585000")


def test_warning_close_debit_multiplier():
    settings = get_settings()
    spread = SpreadPackage(
        qty="1",
        legs=[
            SpreadLeg(symbol="SPY260605P00585000", side="sell", position_intent="sell_to_open"),
            SpreadLeg(symbol="SPY260605P00580000", side="buy", position_intent="buy_to_open"),
        ],
        metadata={"limit_credit": 0.55},
    )
    debit = resolve_warning_close_debit(spread, settings)
    assert debit == round(0.55 * settings.warning_close_multiplier, 2)