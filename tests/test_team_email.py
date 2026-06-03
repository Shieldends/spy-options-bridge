"""Tests for team email subject protocol (no SMTP)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

from team_email import (  # noqa: E402
    PREFIX,
    SUBJECT_ACTION_DONE,
    SUBJECT_PERMISSION,
    SUBJECT_STATUS,
    bridge_notify,
    send_permission,
)


def test_subject_prefixes():
    assert PREFIX == "[SPY-LIVE]"
    assert SUBJECT_STATUS.startswith(PREFIX)
    assert SUBJECT_PERMISSION.endswith("NEEDED")
    assert "ACTION DONE" in SUBJECT_ACTION_DONE


def test_bridge_notify_maps_entry_filled(monkeypatch):
    called: list[tuple[str, str]] = []

    def fake_send(subject: str, body: str, **kwargs):
        called.append((subject, body))
        return True

    monkeypatch.setattr("team_email.send_email_alert", fake_send)
    bridge_notify("Entry Filled", "order abc at $0.55", level="INFO")
    assert called[0][0].startswith(f"{PREFIX} ACTION DONE")


def test_bridge_notify_skips_chasing(monkeypatch):
    called: list[str] = []

    def fake_send(subject: str, body: str, **kwargs):
        called.append(subject)
        return True

    monkeypatch.setattr("team_email.send_email_alert", fake_send)
    assert bridge_notify("Chasing Entry Fill", "…") is False
    assert called == []


def test_permission_body_has_yes_no(monkeypatch):
    bodies: list[str] = []

    def fake_send(subject: str, body: str, **kwargs):
        bodies.append(body)
        return True

    monkeypatch.setattr("team_email.send_email_alert", fake_send)
    send_permission("Approve burst at open?", headline="Burst")
    assert "Reply YES" in bodies[0]
    assert "Reply NO" in bodies[0]
