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
    bridge_notify,
    reset_rate_limits_for_tests,
    send_permission,
    send_permission_request,
    send_status,
    send_team_email,
    _subject,
)


def test_subject_format():
    assert PREFIX == "[SPY Command Center]"
    assert _subject("status", "Command Center started") == (
        "[SPY Command Center] status: Command Center started"
    )
    assert _subject("permission", "Burst") == "[SPY Command Center] permission: Burst"


def test_bridge_notify_maps_entry_filled(monkeypatch):
    called: list[tuple[str, str]] = []

    def fake_send(subject: str, body: str, **kwargs):
        called.append((subject, body))
        return True

    monkeypatch.setattr("team_email.send_email_alert", fake_send)
    monkeypatch.setattr("team_email._append_team_recall", lambda s: None)
    reset_rate_limits_for_tests()
    bridge_notify("Entry Filled", "order abc at $0.55", level="INFO")
    assert called[0][0].startswith(f"{PREFIX} credit:")


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
    monkeypatch.setattr("team_email._append_team_recall", lambda s: None)
    reset_rate_limits_for_tests()
    send_permission("Approve burst at open?", headline="Burst")
    assert "Reply YES" in bodies[0]
    assert "Reply NO" in bodies[0]
    send_permission_request("Sample", "Need OK?")
    assert "Reply YES" in bodies[-1]


def test_general_rate_limit(monkeypatch):
    sent: list[str] = []

    def fake_send(subject: str, body: str, **kwargs):
        sent.append(subject)
        return True

    monkeypatch.setattr("team_email.send_email_alert", fake_send)
    monkeypatch.setattr("team_email._append_team_recall", lambda s: None)
    reset_rate_limits_for_tests()
    assert send_status("First", "one") is True
    assert send_status("Second", "two") is False
    assert len(sent) == 1


def test_permission_bypasses_general_rate_limit(monkeypatch):
    sent: list[str] = []

    def fake_send(subject: str, body: str, **kwargs):
        sent.append(subject)
        return True

    monkeypatch.setattr("team_email.send_email_alert", fake_send)
    monkeypatch.setattr("team_email._append_team_recall", lambda s: None)
    reset_rate_limits_for_tests()
    send_status("A", "body")
    assert send_permission_request("B", "need yes") is True
    assert len(sent) == 2


def test_team_recall_appends_sync(tmp_path, monkeypatch):
    grok = tmp_path / "grok_outbox.md"
    inbox = tmp_path / "cursor_inbox.md"
    monkeypatch.setattr("team_email.SYNC_DIR", tmp_path)
    monkeypatch.setattr("team_email.GROK_OUTBOX", grok)
    monkeypatch.setattr("team_email.CURSOR_INBOX", inbox)
    monkeypatch.setattr(
        "team_email.send_email_alert",
        lambda subject, body, **kwargs: True,
    )
    reset_rate_limits_for_tests()
    send_team_email("report", "Test recall", "body", bypass_rate_limit=True)
    assert grok.read_text(encoding="utf-8").strip().endswith(
        "Email sent: [SPY Command Center] report: Test recall"
    )
    assert "Email sent:" in inbox.read_text(encoding="utf-8")
