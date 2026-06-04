"""Live readiness: auth 401, tv_pause_risk, pre-entry order cleanup."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from main import (
    OrderResult,
    SpreadLeg,
    SpreadPackage,
    build_tv_pause_risk,
    get_settings,
    webhook_auth_json_response,
)


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_webhook_auth_returns_401_for_entry():
    resp = webhook_auth_json_response("Invalid webhook secret", dry_run=False, kind="entry")
    assert resp.status_code == 401
    body = resp.body.decode()
    assert "Invalid webhook secret" in body


def test_build_tv_pause_risk_green_when_ready(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("APCA_API_KEY_ID", "key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "secret")
    get_settings.cache_clear()
    s = get_settings()
    with patch("main._app_started_mono", time.monotonic() - 120):
        risk = build_tv_pause_risk(s, {"open_mleg_count": 0, "open_order_count": 0})
    assert risk["level"] == "green"
    assert risk["webhook_secret_configured"] is True


def test_build_tv_pause_risk_yellow_with_open_mleg(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("APCA_API_KEY_ID", "key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "secret")
    get_settings.cache_clear()
    s = get_settings()
    risk = build_tv_pause_risk(s, {"open_mleg_count": 2, "open_order_count": 2})
    assert risk["level"] == "yellow"
    assert any("open_mleg" in r for r in risk["reasons"])


def test_entry_bad_secret_http_401(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "real-secret")
    monkeypatch.setenv("EXECUTION_MODE", "sandbox")
    get_settings.cache_clear()
    from main import app

    client = TestClient(app)
    r = client.post("/entry", json={"webhookSecret": "wrong", "ticker": "SPY", "action": "PUT_CREDIT_SPREAD"})
    assert r.status_code == 401
    assert r.json().get("success") is False


@pytest.mark.asyncio
async def test_submit_entry_cancels_before_submit(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "x")
    monkeypatch.setenv("APCA_API_KEY_ID", "key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "secret")
    monkeypatch.setenv("AUTO_CANCEL_CONFLICTING_ORDERS", "true")
    get_settings.cache_clear()
    settings = get_settings()

    from main import TradingViewSignal, submit_entry_from_signal

    signal = TradingViewSignal.model_validate(
        {
            "ticker": "SPY",
            "signalPrice": 590.0,
            "action": "PUT_CREDIT_SPREAD",
            "dteFilter": "weekly",
            "strikeOffsetShort": -5,
            "strikeOffsetLong": -6,
            "limitCredit": 0.35,
        }
    )

    spread = SpreadPackage(
        qty="1",
        legs=[
            SpreadLeg(symbol="SPY260605P00585000", side="sell", position_intent="sell_to_open"),
            SpreadLeg(symbol="SPY260605P00580000", side="buy", position_intent="buy_to_open"),
        ],
        metadata={
            "strategy": "PUT_CREDIT_SPREAD",
            "short_strike": 585.0,
            "long_strike": 580.0,
            "expiration": "2026-06-05",
            "limit_credit": 0.35,
        },
    )

    with (
        patch("main.build_spread", return_value=spread),
        patch("main.align_spread_to_alpaca", new=AsyncMock(return_value=spread)),
        patch("main.resolve_entry_limit_credit", new=AsyncMock(return_value=spread)),
        patch("main.cancel_alpaca_open_orders_for_symbols", new=AsyncMock(return_value=1)) as sym_cancel,
        patch("main.cancel_open_mleg_orders", new=AsyncMock(return_value=2)) as mleg_cancel,
        patch(
            "main.submit_order",
            new=AsyncMock(
                return_value=OrderResult(success=True, message="ok", dry_run=False, payload={}),
            ),
        ),
    ):
        await submit_entry_from_signal(settings, signal)
        sym_cancel.assert_awaited_once()
        mleg_cancel.assert_awaited_once()
