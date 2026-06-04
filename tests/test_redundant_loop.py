"""Tests for redundant test loop stop conditions."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import redundant_test_loop as rtl  # noqa: E402


def test_should_stop_on_stop_file(tmp_path, monkeypatch):
    stop = tmp_path / "STOP-REDUNDANT-TESTS.txt"
    stop.write_text("stop", encoding="utf-8")
    monkeypatch.setattr(rtl, "STOP_FILE", stop)
    reason = rtl.should_stop()
    assert reason is not None
    assert "STOP file" in reason


def test_should_not_stop_when_session_closed(tmp_path, monkeypatch):
    """After 16:00 ET redundant loop must keep running (not same-day 9:30 compare)."""
    stop = tmp_path / "STOP-REDUNDANT-TESTS.txt"
    monkeypatch.setattr(rtl, "STOP_FILE", stop)
    monkeypatch.setattr(rtl, "market_session_open", lambda _now=None: False)
    assert rtl.should_stop() is None


def test_should_stop_during_regular_session(tmp_path, monkeypatch):
    stop = tmp_path / "STOP-REDUNDANT-TESTS.txt"
    monkeypatch.setattr(rtl, "STOP_FILE", stop)
    monkeypatch.setattr(rtl, "market_session_open", lambda _now=None: True)
    reason = rtl.should_stop()
    assert reason is not None
    assert "Market open" in reason
