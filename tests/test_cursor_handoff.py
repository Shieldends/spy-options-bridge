"""Cursor handoff writes into sync inbox."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cursor_handoff as ch  # noqa: E402


def test_insert_after_rules_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "SYNC_DIR", tmp_path)
    monkeypatch.setattr(ch, "CURSOR_INBOX", tmp_path / "cursor_inbox.md")
    monkeypatch.setattr(ch, "GROK_OUTBOX", tmp_path / "grok_outbox.md")
    monkeypatch.setattr(ch, "INBOX_JSONL", tmp_path / "inbox.jsonl")
    monkeypatch.setattr(ch, "DESKTOP_HANDOFF", tmp_path / "handoff.txt")
    monkeypatch.setattr(ch, "run_append_sync", lambda: True)

    (tmp_path / "cursor_inbox.md").write_text(
        "## RULES + ACTIONS COMPLETE\n\nold line\n",
        encoding="utf-8",
    )
    ch.handoff("Test fix", ["bullet A"], next_step="do B", run_sync=False, mirror_grok=False)
    text = (tmp_path / "cursor_inbox.md").read_text(encoding="utf-8")
    assert "FIX handoff" in text
    assert "bullet A" in text
    assert "old line" in text
    assert (tmp_path / "inbox.jsonl").read_text(encoding="utf-8").strip()


def test_drain_pending_fixes(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "SYNC_DIR", tmp_path)
    monkeypatch.setattr(ch, "FIXES_PENDING", tmp_path / "fixes_pending.jsonl")
    monkeypatch.setattr(ch, "CURSOR_INBOX", tmp_path / "cursor_inbox.md")
    monkeypatch.setattr(ch, "GROK_OUTBOX", tmp_path / "grok_outbox.md")
    monkeypatch.setattr(ch, "INBOX_JSONL", tmp_path / "inbox.jsonl")
    monkeypatch.setattr(ch, "DESKTOP_HANDOFF", tmp_path / "handoff.txt")
    monkeypatch.setattr(ch, "run_append_sync", lambda: True)
    (tmp_path / "cursor_inbox.md").write_text("## RULES + ACTIONS COMPLETE\n", encoding="utf-8")

    entry = {"topic": "Q", "bullets": ["x"], "next": ""}
    (tmp_path / "fixes_pending.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
    assert ch.drain_pending_fixes() == 1
    assert "FIX handoff" in (tmp_path / "cursor_inbox.md").read_text(encoding="utf-8")
