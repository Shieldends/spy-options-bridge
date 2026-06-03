"""Tests for optional SMTP email alerts."""

from __future__ import annotations

from email_alerts import email_configured, send_email_alert


def test_email_disabled_returns_false():
    assert send_email_alert("test", "body", settings={"email_enabled": False}) is False


def test_email_missing_host_not_configured():
    cfg = {
        "email_enabled": True,
        "smtp_host": "",
        "email_from": "a@b.com",
        "email_to": "a@b.com",
    }
    assert email_configured(cfg) is False
    assert send_email_alert("test", "body", settings=cfg) is False
