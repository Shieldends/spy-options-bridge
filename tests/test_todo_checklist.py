"""Tests for pre-market todo checklist."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import todo_checklist as tc  # noqa: E402

before_market_open = tc.before_market_open
incomplete_items = tc.incomplete_items
load_checklist = tc.load_checklist
mark_done = tc.mark_done


@pytest.fixture
def checklist_file(tmp_path, monkeypatch):
    path = tmp_path / "USER-TODO-CHECKLIST.json"
    monkeypatch.setattr("todo_checklist.CHECKLIST_PATH", path)
    path.write_text(
        json.dumps(
            {
                "items": {
                    "email_setup_done": False,
                    "email_test_done": False,
                    "render_email_env": False,
                    "dual_sync_running": False,
                    "keepalive_running": False,
                    "tradingview_alerts_confirmed": True,
                    "alpaca_old_orders_canceled": True,
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def test_incomplete_lists_open_items(checklist_file):
    missing = incomplete_items()
    assert "email_setup_done" in missing
    assert "tradingview_alerts_confirmed" not in missing


def test_mark_done(checklist_file):
    mark_done("email_setup_done")
    data = load_checklist()
    assert data["items"]["email_setup_done"] is True
    assert "email_setup_done" not in incomplete_items()


def test_before_market_open_weekday_morning():
    et = ZoneInfo("America/New_York")
    morning = datetime(2026, 6, 4, 8, 0, tzinfo=et)
    assert before_market_open(morning) is True
    after = datetime(2026, 6, 4, 10, 0, tzinfo=et)
    assert before_market_open(after) is False
