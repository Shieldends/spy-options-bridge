from app.config import Settings
from app.models import MLegOrderPackage
from app.risk_management import build_take_profit_close_payload, check_danger_zone
from app.spread_builder import build_credit_spread_order, coerce_signal


def test_danger_zone_triggers():
    is_danger, msg = check_danger_zone(579.5, 580.0, danger_pct=0.01, ticker="SPY")
    assert is_danger is True
    assert "CRITICAL" in msg


def test_danger_zone_safe():
    is_danger, _ = check_danger_zone(590.0, 579.0, danger_pct=0.01, ticker="SPY")
    assert is_danger is False


def test_take_profit_at_50_percent():
    settings = Settings(
        _env_file=None,
        DEFAULT_LIMIT_CREDIT=0.50,
    )
    signal = coerce_signal(
        {
            "ticker": "SPY",
            "action": "PUT_CREDIT_SPREAD",
            "short_strike": 579,
            "long_strike": 578,
            "expiration": "2026-06-03",
            "limitCredit": 0.50,
        },
        settings,
    )
    entry = build_credit_spread_order(signal, settings)
    tp = build_take_profit_close_payload(entry, take_profit_pct=0.50)

    assert tp["price-effect"] == "Debit"
    assert tp["price"] == "0.25"
    assert tp["legs"][0]["action"] == "Buy to Close"
    assert tp["legs"][1]["action"] == "Sell to Close"
    assert tp["_meta"]["profit_locked"] == 0.25
