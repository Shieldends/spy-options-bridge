"""Email reply grant — parser, pending actions, process_reply."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import email_reply_grant as erg  # noqa: E402
import operator_gateway as og  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def email_env(tmp_path, monkeypatch):
    cc = tmp_path / "SPY-Command-Center"
    cc.mkdir()
    grant = tmp_path / "OPERATOR-GRANT.json"
    audit = tmp_path / "OPERATOR-AUDIT.log"
    cfg_src = ROOT / "config" / "operator_protocol.yaml"
    cfg_copy = tmp_path / "operator_protocol.yaml"
    text = cfg_src.read_text(encoding="utf-8")
    cfg_copy.write_text(text, encoding="utf-8")
    cfg = yaml.safe_load(text)
    cfg["paths"]["grant_file"] = str(grant)
    cfg["paths"]["audit_log"] = str(audit)
    cfg["paths"]["user_root"] = str(tmp_path)
    cfg["paths"]["command_center_folder"] = str(cc)
    cfg["email_command"]["pending_file"] = str(cc / "OPERATOR-PENDING.json")
    cfg["email_command"]["processed_uids_file"] = str(cc / "OPERATOR-EMAIL-PROCESSED.json")
    cfg_copy.write_text(yaml.dump(cfg), encoding="utf-8")
    monkeypatch.setenv("OPERATOR_PROTOCOL_CONFIG", str(cfg_copy))
    monkeypatch.setenv("OPERATOR_GRANT_FILE", str(grant))
    monkeypatch.setenv("OPERATOR_AUDIT_LOG", str(audit))
    return {"grant": grant, "audit": audit, "cfg": cfg, "cc": cc}


def test_extract_keywords_yes_ok_deploy():
    assert erg.extract_keywords("Yes please deploy now") == ["YES", "DEPLOY"]
    assert erg.extract_keywords("ok") == ["OK"]


def test_normalize_reply_strips_quoted():
    body = "YES approve\n\nOn Thu wrote:\n> old stuff"
    assert erg.normalize_reply_body(body) == "YES approve"


def test_map_keywords_grant_and_deploy(email_env):
    actions = erg.map_keywords_to_actions(["YES", "DEPLOY"], email_env["cfg"])
    assert "grant_session" in actions
    assert "render_deploy_nudge" in actions
    assert "approve_pending" in actions


def test_map_keywords_no_denies(email_env):
    actions = erg.map_keywords_to_actions(["NO"], email_env["cfg"])
    assert actions == ["deny_pending"]


def test_create_and_approve_pending(email_env):
    entry = erg.create_pending_request(
        email_env["cfg"],
        kind="deploy_bundle",
        title="Test deploy",
    )
    assert entry["status"] == "pending"
    ok, pid = erg.approve_pending(email_env["cfg"], entry["id"])
    assert ok
    assert pid == entry["id"]
    store = erg.load_pending_store(email_env["cfg"])
    approved = [r for r in store["requests"] if r["id"] == entry["id"]][0]
    assert approved["status"] == "approved"


def test_process_reply_yes_grants_session(email_env):
    pending = erg.create_pending_request(
        email_env["cfg"],
        kind="deploy_bundle",
        title="Deploy",
    )
    subject = f"[SPY Command Center] NEED APPROVAL - Deploy PENDING-ID: {pending['id']}"
    result = erg.process_reply(
        from_addr="Shield Inc <shieldinc850@gmail.com>",
        subject=subject,
        body="YES",
        cfg=email_env["cfg"],
    )
    assert result["ok"] is True
    grant = og.read_grant(email_env["cfg"])
    ok, _ = og.grant_status(grant)
    assert ok
    assert grant["granted_by"] == "email-reply"
    exp = datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    assert exp > datetime.now(timezone.utc) + timedelta(hours=11)


def test_process_reply_rejects_wrong_sender(email_env):
    result = erg.process_reply(
        from_addr="attacker@evil.example",
        subject="[SPY Command Center] NEED APPROVAL - hack",
        body="YES",
        cfg=email_env["cfg"],
    )
    assert result["ok"] is False
    assert not email_env["grant"].is_file()


def test_process_reply_deploy_writes_markers(email_env):
    pending = erg.create_pending_request(
        email_env["cfg"],
        kind="deploy_bundle",
        title="Deploy bundle",
    )
    subject = f"[SPY Command Center] NEED APPROVAL - Deploy PENDING-ID: {pending['id']}"
    result = erg.process_reply(
        from_addr="shieldinc850@gmail.com",
        subject=subject,
        body="DEPLOY",
        cfg=email_env["cfg"],
    )
    assert result["ok"] is True
    assert (email_env["cc"] / "DEPLOY-APPROVED.txt").is_file()
    assert (email_env["cc"] / "GIT-PUSH-APPROVED.txt").is_file()


def test_process_reply_stop_writes_stop_file(email_env):
    desktop = Path(email_env["cfg"]["paths"]["user_root"]) / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    result = erg.process_reply(
        from_addr="shieldinc850@gmail.com",
        subject="[SPY Command Center] NEED APPROVAL - stop",
        body="STOP",
        cfg=email_env["cfg"],
    )
    assert result["ok"] is True
    stop = desktop / "STOP-REDUNDANT-TESTS.txt"
    assert stop.is_file()


def test_awaiting_email_ok(email_env):
    assert not erg.awaiting_email_ok(email_env["cfg"])
    erg.create_pending_request(email_env["cfg"], kind="x", title="wait")
    assert erg.awaiting_email_ok(email_env["cfg"])
    assert erg.pending_summary(email_env["cfg"]).startswith("awaiting")


def test_extract_pending_id():
    assert erg.extract_pending_id("Re: hi", "PENDING-ID: abc-123\nYES") == "abc-123"
