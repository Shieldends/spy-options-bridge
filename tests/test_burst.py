"""Tests for paper burst helpers."""
from __future__ import annotations

import pytest

from main import (
    _burst_paper_guard,
    _parse_burst_count,
    get_settings,
    webhook_auth_error,
)


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_parse_burst_count_from_json():
    payload = {"burstCount": 10, "ticker": "SPY"}
    assert _parse_burst_count(payload, None) == 10
    assert "burstCount" not in payload


def test_parse_burst_count_from_query():
    payload = {"ticker": "SPY"}
    assert _parse_burst_count(payload, 25, max_count=25) == 25
    assert _parse_burst_count(payload, 25) == 10  # default cap


def test_parse_burst_count_clamps():
    assert _parse_burst_count({"burstCount": 999}, None, max_count=10) == 10
    assert _parse_burst_count({"burstCount": 999}, None, max_count=200) == 200


def test_burst_paper_guard_requires_paper(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("APCA_API_BASE_URL", "https://api.alpaca.markets")
    s = get_settings()
    assert _burst_paper_guard(s) is not None


def test_webhook_auth_for_burst():
    assert webhook_auth_error("secret", "secret") is None
    assert webhook_auth_error(None, "secret") is not None
