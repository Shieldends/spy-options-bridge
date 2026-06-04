"""Tests for SPY-LIVE-COMMAND helpers."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import command_center as cc  # noqa: E402


def test_status_email_default(monkeypatch):
    monkeypatch.delenv("EMAIL_TO", raising=False)
    assert cc.status_email() == cc.DEFAULT_STATUS_EMAIL


def test_status_email_from_env(monkeypatch):
    monkeypatch.setenv("EMAIL_TO", "user@example.com")
    assert cc.status_email() == "user@example.com"


def test_worker_command_includes_redundant_interval():
    cmd = cc.worker_command("redundant_test_loop.py", ["--interval", "300"])
    joined = " ".join(cmd)
    assert "redundant_test_loop.py" in joined
    assert "--interval" in cmd
    assert "300" in cmd


def test_team_ready_core_without_redundant(monkeypatch):
    monkeypatch.setattr(
        cc,
        "team_worker_status",
        lambda: {
            "dual_sync_loop.py": True,
            "bridge_keepalive.py": True,
            "redundant_test_loop.py": False,
        },
    )
    monkeypatch.setattr(cc, "_market_session_open_now", lambda: True)
    assert cc.team_ready_for_display() is True


def test_fetch_health_parses_version(monkeypatch):
    class FakeResp:
        status = 200

        def read(self):
            return b'{"version":"9.9.9","configured":true}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(cc.urllib.request, "urlopen", lambda *a, **k: FakeResp())
    ok, detail = cc.fetch_health()
    assert ok is True
    assert "9.9.9" in detail
    assert "configured=True" in detail
