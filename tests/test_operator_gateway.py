"""Operator gateway — grant, deny, whitelist."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import operator_gateway as og  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def op_env(tmp_path, monkeypatch):
    grant = tmp_path / "OPERATOR-GRANT.json"
    audit = tmp_path / "OPERATOR-AUDIT.log"
    cfg_src = ROOT / "config" / "operator_protocol.yaml"
    cfg_copy = tmp_path / "operator_protocol.yaml"
    cfg_copy.write_text(cfg_src.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("OPERATOR_PROTOCOL_CONFIG", str(cfg_copy))
    monkeypatch.setenv("OPERATOR_GRANT_FILE", str(grant))
    monkeypatch.setenv("OPERATOR_AUDIT_LOG", str(audit))
    cfg = yaml.safe_load(cfg_copy.read_text(encoding="utf-8"))
    return {"grant": grant, "audit": audit, "cfg": cfg}


def test_deny_without_grant(op_env):
    rc = og.run_action("health", "https://spy-options-bridge.onrender.com/health", op_env["cfg"])
    assert rc == 1
    assert "denied" in op_env["audit"].read_text(encoding="utf-8")


def test_grant_expiry(op_env):
    expired = {
        "tier": "session",
        "granted_at": "2020-01-01T00:00:00+00:00",
        "expires_at": "2020-01-01T00:01:00+00:00",
        "granted_by": "test",
    }
    op_env["grant"].write_text(json.dumps(expired), encoding="utf-8")
    rc = og.run_action(
        "launch",
        r"C:\Users\Shiel\Desktop\RUN-LAB.bat",
        op_env["cfg"],
    )
    assert rc == 1
    ok, reason = og.grant_status(og.read_grant(op_env["cfg"]))
    assert not ok
    assert reason == "grant expired"


def test_allow_launch_whitelist(op_env, monkeypatch):
    og.write_grant("session", op_env["cfg"], source="test")
    monkeypatch.setattr(og, "execute_action", lambda a, t, c: (True, "mocked"))
    target = r"C:\Users\Shiel\Desktop\RUN-LAB.bat"
    rc = og.run_action("launch", target, op_env["cfg"])
    assert rc == 0
    audit = op_env["audit"].read_text(encoding="utf-8")
    assert "launch" in audit
    assert "ok" in audit


def test_tier_blocks_shell_for_launch_grant(op_env, monkeypatch):
    og.write_grant("launch", op_env["cfg"], source="test")
    monkeypatch.setattr(og, "execute_action", lambda a, t, c: (True, "mocked"))
    rc = og.run_action("shell", "pytest tests/ -q", op_env["cfg"])
    assert rc == 1


def test_validate_launch_rejects_outside_whitelist(op_env):
    og.write_grant("session", op_env["cfg"], source="test")
    ok, msg = og.validate_target("launch", r"C:\Users\Shiel\Desktop\EVIL.bat", op_env["cfg"])
    assert not ok
    assert "whitelist" in msg


def test_write_grant_future_expiry(op_env):
    og.write_grant("session", op_env["cfg"], source="test")
    grant = og.read_grant(op_env["cfg"])
    assert grant is not None
    ok, _ = og.grant_status(grant)
    assert ok
    exp = datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    assert exp > datetime.now(timezone.utc) + timedelta(minutes=55)


def test_validate_program_allows_cursor_alias(op_env):
    ok, msg = og.validate_target("program", "cursor", op_env["cfg"])
    assert ok, msg


def test_validate_program_denies_unknown_exe(op_env):
    ok, msg = og.validate_target(
        "program",
        r"C:\Users\Shiel\Desktop\malware.exe",
        op_env["cfg"],
    )
    assert not ok
    assert "whitelist" in msg


def test_validate_url_allows_tradingview(op_env):
    ok, msg = og.validate_target("url", "https://www.tradingview.com/chart/", op_env["cfg"])
    assert ok, msg


def test_validate_url_denies_random(op_env):
    ok, msg = og.validate_target("url", "https://evil.example/phish", op_env["cfg"])
    assert not ok
    assert "whitelist" in msg


def test_tier_blocks_program_for_observe(op_env, monkeypatch):
    og.write_grant("observe", op_env["cfg"], source="test")
    monkeypatch.setattr(og, "execute_action", lambda a, t, c: (True, "mocked"))
    rc = og.run_action("program", "cursor", op_env["cfg"])
    assert rc == 1


def test_allow_program_with_session_grant(op_env, monkeypatch):
    og.write_grant("session", op_env["cfg"], source="test")
    monkeypatch.setattr(og, "execute_action", lambda a, t, c: (True, "mocked"))
    rc = og.run_action("program", "cursor_project", op_env["cfg"])
    assert rc == 0


def test_allow_url_with_session_grant(op_env, monkeypatch):
    og.write_grant("session", op_env["cfg"], source="test")
    monkeypatch.setattr(og, "execute_action", lambda a, t, c: (True, "mocked"))
    rc = og.run_action("url", "https://app.alpaca.markets/paper/dashboard", op_env["cfg"])
    assert rc == 0


def test_service_denied_when_whitelist_empty(op_env):
    og.write_grant("session", op_env["cfg"], source="test")
    ok, msg = og.validate_target("service_start", "SomeService", op_env["cfg"])
    assert not ok
    assert "whitelist" in msg
