"""Tests for guardian control."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cc_guardian_ctl as gctl  # noqa: E402


def test_guardian_running_false_when_no_pid_file(tmp_path, monkeypatch):
    monkeypatch.setattr(gctl, "GUARDIAN_PID", tmp_path / "missing.pid")
    assert gctl.guardian_running() is False


def test_read_write_pid(tmp_path, monkeypatch):
    pid_file = tmp_path / "g.pid"
    monkeypatch.setattr(gctl, "GUARDIAN_PID", pid_file)
    gctl.write_guardian_pid(12345)
    assert gctl.read_guardian_pid() == 12345
    gctl.clear_guardian_pid()
    assert gctl.read_guardian_pid() is None


def test_start_skips_if_running(monkeypatch):
    monkeypatch.setattr(gctl, "guardian_running", lambda: True)
    monkeypatch.setattr(gctl, "read_guardian_pid", lambda: 99)
    ok, msg = gctl.start_guardian()
    assert ok is True
    assert "already" in msg.lower()
