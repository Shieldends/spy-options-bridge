"""Pre-open matrix must not fire live /entry unless env explicitly set."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_preopen_matrix as rpm  # noqa: E402


def test_aggressive_off_by_default(monkeypatch):
    monkeypatch.delenv("PRE_OPEN_TEST_AGGRESSIVE", raising=False)
    assert rpm.pre_open_test_aggressive() is False


def test_aggressive_only_when_env_true(monkeypatch):
    monkeypatch.setenv("PRE_OPEN_TEST_AGGRESSIVE", "true")
    assert rpm.pre_open_test_aggressive() is True
    monkeypatch.setenv("PRE_OPEN_TEST_AGGRESSIVE", "false")
    assert rpm.pre_open_test_aggressive() is False


def test_pressure_yaml_does_not_enable_aggressive(monkeypatch):
    monkeypatch.delenv("PRE_OPEN_TEST_AGGRESSIVE", raising=False)
    assert rpm.pre_open_pressure_enabled() is True
    assert rpm.pre_open_test_aggressive() is False
