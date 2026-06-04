"""arm_for_open — operator grant write (mocked)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import arm_for_open as afo  # noqa: E402
import operator_gateway as og  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def arm_env(tmp_path, monkeypatch):
    grant = tmp_path / "OPERATOR-GRANT.json"
    audit = tmp_path / "OPERATOR-AUDIT.log"
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    sync = tmp_path / "sync"
    sync.mkdir()
    log = desktop / "ARM-FOR-OPEN.log"
    cfg_src = ROOT / "config" / "operator_protocol.yaml"
    cfg_copy = tmp_path / "operator_protocol.yaml"
    text = cfg_src.read_text(encoding="utf-8")
    cfg_copy.write_text(text, encoding="utf-8")
    cfg = yaml.safe_load(text)
    cfg["paths"]["grant_file"] = str(grant)
    cfg["paths"]["audit_log"] = str(audit)
    cfg_copy.write_text(yaml.dump(cfg), encoding="utf-8")
    monkeypatch.setenv("OPERATOR_PROTOCOL_CONFIG", str(cfg_copy))
    monkeypatch.setenv("OPERATOR_GRANT_FILE", str(grant))
    monkeypatch.setenv("OPERATOR_AUDIT_LOG", str(audit))
    monkeypatch.setattr(afo, "DESKTOP", desktop)
    monkeypatch.setattr(afo, "LOG_PATH", log)
    monkeypatch.setattr(afo, "SYNC_DIR", sync)
    monkeypatch.setattr(afo, "STOP_FILE", desktop / "STOP-REDUNDANT-TESTS.txt")
    return {"grant": grant, "cfg": cfg, "desktop": desktop, "sync": sync, "log": log}


def test_arm_writes_session_grant_12h(arm_env):
    cfg = og.load_config()
    path = afo.write_arm_grant(cfg)
    assert path == arm_env["grant"]
    grant = json.loads(path.read_text(encoding="utf-8"))
    assert grant["tier"] == "session"
    assert grant["granted_by"] == "arm-for-open"
    exp = datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    hours = (exp - datetime.now(timezone.utc)).total_seconds() / 3600
    assert 11.5 <= hours <= 12.5


def test_arm_full_flow_mocked(arm_env):
    stop = arm_env["desktop"] / "STOP-REDUNDANT-TESTS.txt"
    stop.write_text("stop\n", encoding="utf-8")
    with (
        patch.object(afo, "spawn_command_center"),
        patch.object(afo, "register_burst_task"),
        patch.object(afo, "confirm_arm", return_value=True),
    ):
        rc = afo.arm(yes=True)
    assert rc == 0
    assert arm_env["grant"].is_file()
    assert not stop.exists()
    outbox = arm_env["sync"] / "grok_outbox.md"
    assert outbox.is_file()
    assert "ARMED minimal mode" in outbox.read_text(encoding="utf-8")
    assert "grant written" in arm_env["log"].read_text(encoding="utf-8")


def test_auto_grant_from_marker(arm_env):
    marker = arm_env["desktop"] / "OPERATOR-AUTO-ARM.txt"
    marker.write_text("", encoding="utf-8")
    og_cfg = og.load_config()
    og_cfg["paths"]["user_root"] = str(arm_env["desktop"].parent)
    path = og.try_auto_grant_from_marker(og_cfg)
    assert path is not None
    grant = json.loads(path.read_text(encoding="utf-8"))
    assert grant["granted_by"] == "auto-arm-marker"
