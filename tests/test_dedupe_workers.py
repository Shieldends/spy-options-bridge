"""Dedupe helper selection — newest process wins."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import dedupe_spy_workers as dedupe  # noqa: E402


def test_choose_keep_honors_exclude(monkeypatch):
    monkeypatch.setattr(dedupe, "process_creation_ts", lambda pid: float(pid))
    keep = dedupe.choose_keep([10, 20, 30], {20}, "command_center.py")
    assert keep == 20


def test_choose_keep_picks_newest_by_creation_time(monkeypatch):
    monkeypatch.setattr(
        dedupe,
        "process_creation_ts",
        lambda pid: {100: 1.0, 200: 3.0, 300: 2.0}[pid],
    )
    keep = dedupe.choose_keep([100, 200, 300], set(), "command_center.py")
    assert keep == 200
