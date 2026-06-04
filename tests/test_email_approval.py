"""Email approval — parser, Desktop grant/pending files, process_reply."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import email_approval as ea  # noqa: E402
import operator_gateway as og  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def email_env(tmp_path, monkeypatch):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    cc = tmp_path / "SPY-Command-Center"
    cc.mkdir()
    grant = desktop / "OPERATOR-GRANT.json"
    pending = desktop / "PENDING-ACTION.json"
    audit = tmp_path / "OPERATOR-AUDIT.log"
    cfg_src = ROOT / "config" / "operator_protocol.yaml"
    cfg_copy = tmp_path / "operator_protocol.yaml"
    text = cfg_src.read_text(encoding="utf-8")
    cfg = yaml.safe_load(text)
    cfg["paths"]["grant_file"] = str(grant)
    cfg["paths"]["audit_log"] = str(audit)
    cfg["paths"]["user_root"] = str(tmp_path)
    cfg["paths"]["command_center_folder"] = str(cc)
    cfg["email_command"]["pending_file"] = str(pending)
    cfg["email_command"]["processed_uids_file"] = str(cc / "OPERATOR-EMAIL-PROCESSED.json")
    cfg_copy.write_text(yaml.dump(cfg), encoding="utf-8")
    monkeypatch.setenv("OPERATOR_PROTOCOL_CONFIG", str(cfg_copy))
    monkeypatch.setenv("OPERATOR_GRANT_FILE", str(grant))
    monkeypatch.setenv("OPERATOR_AUDIT_LOG", str(audit))
    return {"grant": grant, "pending": pending, "audit": audit, "cfg": cfg, "cc": cc, "desktop": desktop}


def test_extract_keywords_yes_ok_deploy():
    assert ea.extract_keywords("Yes please deploy now") == ["YES", "DEPLOY"]
    assert ea.extract_keywords("ok") == ["OK"]


def test_normalize_reply_strips_quoted():
    body = "YES approve\n\nOn Thu wrote:\n> old stuff"
    assert ea.normalize_reply_body(body) == "YES approve"


def test_map_keywords_from_yaml(email_env):
    actions = ea.map_keywords_to_actions(["YES", "DEPLOY"], email_env["cfg"])
    assert "grant_session" in actions
    assert "render_deploy_nudge" in actions


def test_create_pending_writes_desktop_file(email_env):
    entry = ea.create_pending_request(
        email_env["cfg"],
        kind="deploy_bundle",
        title="Test deploy",
    )
    assert email_env["pending"].is_file()
    store = json.loads(email_env["pending"].read_text(encoding="utf-8"))
    pending = [r for r in store["requests"] if r["id"] == entry["id"]][0]
    assert pending["status"] == "pending"


def test_process_reply_yes_writes_grant(email_env):
    pending = ea.create_pending_request(
        email_env["cfg"],
        kind="deploy_bundle",
        title="Deploy",
    )
    subject = f"[SPY Command Center] NEED APPROVAL - Deploy PENDING-ID: {pending['id']}"
    result = ea.process_reply(
        from_addr="Shield Inc <shieldinc850@gmail.com>",
        subject=subject,
        body="YES",
        cfg=email_env["cfg"],
    )
    assert result["ok"] is True
    assert email_env["grant"].is_file()
    grant = og.read_grant(email_env["cfg"])
    ok, _ = og.grant_status(grant)
    assert ok
    assert grant["granted_by"] == "email-reply"
    exp = datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    assert exp > datetime.now(timezone.utc) + timedelta(hours=11)


def test_process_reply_rejects_wrong_sender(email_env):
    result = ea.process_reply(
        from_addr="attacker@evil.example",
        subject="[SPY Command Center] NEED APPROVAL - hack",
        body="YES",
        cfg=email_env["cfg"],
    )
    assert result["ok"] is False
    assert not email_env["grant"].is_file()


def test_process_reply_deploy_writes_markers(email_env):
    pending = ea.create_pending_request(
        email_env["cfg"],
        kind="deploy_bundle",
        title="Deploy bundle",
    )
    subject = f"[SPY Command Center] NEED APPROVAL - Deploy bundle PENDING-ID: {pending['id']}"
    result = ea.process_reply(
        from_addr="shieldinc850@gmail.com",
        subject=subject,
        body="DEPLOY",
        cfg=email_env["cfg"],
    )
    assert result["ok"] is True
    assert (email_env["cc"] / "DEPLOY-APPROVED.txt").is_file()


def test_send_sms_noop_without_twilio(monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    assert ea.send_sms("test") is False


def test_ensure_control_doc(email_env):
    path = ea.ensure_email_command_control_doc(email_env["cfg"])
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "YES" in text
    assert "PENDING-ACTION" in text
    assert "need approval" in text.lower()


def test_send_approval_needed_subject(monkeypatch):
    import team_email as te  # noqa: E402

    captured: list[str] = []

    def fake_send(subject: str, body: str, **kwargs):
        captured.append(subject)
        return True

    monkeypatch.setattr(te, "send_email_alert", fake_send)
    monkeypatch.setattr(te, "_append_team_recall", lambda s: None)
    te.reset_rate_limits_for_tests()
    assert te.send_approval_needed("pending-abc", "Deploy bundle approval", "detail") is True
    assert len(captured) == 1
    assert "need approval" in captured[0].lower()
    assert captured[0].endswith("Deploy bundle approval")
