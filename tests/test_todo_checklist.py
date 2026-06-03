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
toggle_item = tc.toggle_item
set_item = tc.set_item


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


def test_toggle_item(checklist_file):
    assert toggle_item("email_setup_done") is True
    assert load_checklist()["items"]["email_setup_done"] is True
    assert toggle_item("email_setup_done") is False
    assert load_checklist()["items"]["email_setup_done"] is False


def test_set_item_false(checklist_file):
    mark_done("dual_sync_running")
    set_item("dual_sync_running", False)
    assert load_checklist()["items"]["dual_sync_running"] is False


def test_format_user_live_lines_team_off(checklist_file):
    lines = tc.format_user_live_lines(team_running=False)
    assert len(lines) <= 3
    assert any("START TEAM" in line for line in lines)


def test_format_user_live_lines_team_on(checklist_file):
    lines = tc.format_user_live_lines(team_running=True)
    assert lines == []


def test_ensure_live_defaults(checklist_file):
    set_item("tradingview_alerts_confirmed", False)
    tc.ensure_live_defaults()
    data = load_checklist()
    assert data["items"]["tradingview_alerts_confirmed"] is True


def test_before_market_open_weekday_morning():
    et = ZoneInfo("America/New_York")
    morning = datetime(2026, 6, 4, 8, 0, tzinfo=et)
    assert before_market_open(morning) is True
    after = datetime(2026, 6, 4, 10, 0, tzinfo=et)
    assert before_market_open(after) is False
