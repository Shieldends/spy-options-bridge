#!/usr/bin/env python3
"""Push bridge fixes into Grok/Cursor sync so the next Cursor session inherits context."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SYNC_DIR = Path(r"C:\Users\Shiel\Projects\spy-hybrid-v3\sync")
CURSOR_INBOX = SYNC_DIR / "cursor_inbox.md"
GROK_OUTBOX = SYNC_DIR / "grok_outbox.md"
INBOX_JSONL = SYNC_DIR / "inbox.jsonl"
FIXES_PENDING = SYNC_DIR / "fixes_pending.jsonl"
APPEND_SYNC = Path(r"C:\Users\Shiel\Projects\spy-hybrid-v3\scripts\append_sync.py")
DESKTOP_HANDOFF = Path(r"C:\Users\Shiel\Desktop\CURSOR-LAST-HANDOFF.txt")
ET = ZoneInfo("America/New_York")

RULES_MARKERS = (
    "## 2026-06-04 RULES + ACTIONS COMPLETE",
    "## RULES + ACTIONS COMPLETE",
)


def _ts_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _format_block(
    topic: str,
    bullets: list[str],
    *,
    next_step: str,
    source: str,
) -> str:
    lines = [
        f"### [{_ts_et()}] FIX handoff ({source})",
        f"**Topic:** {topic}",
        "",
    ]
    for b in bullets:
        b = b.strip()
        if b:
            lines.append(f"- {b}")
    if next_step.strip():
        lines.append(f"- **Next:** {next_step.strip()}")
    lines.extend(
        [
            "",
            "**Cursor:** Read `@sync/cursor_inbox.md` (RULES + ACTIONS COMPLETE) and `@sync/grok_outbox.md`.",
            f"**Repo:** `C:\\Users\\Shiel\\spy-options-bridge`",
            "",
        ]
    )
    return "\n".join(lines)


def _grok_sync_json(topic: str, bullets: list[str], next_step: str) -> str:
    payload = {
        "date": date.today().isoformat(),
        "topic": topic,
        "bullets": bullets,
        "next": next_step or "Continue from cursor_inbox FIX handoff block",
        "source": "spy-options-bridge",
    }
    return "GROK_SYNC_UPDATE\n```json\n" + json.dumps(payload, indent=2) + "\n```\n"


def _insert_after_rules_marker(text: str, block: str) -> str:
    for marker in RULES_MARKERS:
        if marker in text:
            idx = text.index(marker) + len(marker)
            return text[:idx] + "\n\n" + block.rstrip() + "\n" + text[idx:]
    return text.rstrip() + "\n\n" + block


def _append_inbox_jsonl(topic: str, bullets: list[str], next_step: str) -> None:
    SYNC_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "date": date.today().isoformat(),
        "topic": topic,
        "bullets": bullets,
        "next": next_step,
        "source": "spy-options-bridge-handoff",
    }
    with INBOX_JSONL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_append_sync() -> bool:
    if not APPEND_SYNC.is_file():
        return False
    py = Path(sys.executable)
    proc = subprocess.run(
        [str(py), str(APPEND_SYNC)],
        cwd=str(APPEND_SYNC.parent.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    return proc.returncode == 0


def handoff(
    topic: str,
    bullets: list[str],
    *,
    next_step: str = "",
    source: str = "spy-options-bridge",
    run_sync: bool = True,
    mirror_grok: bool = True,
) -> Path:
    """Append fix context for Cursor + Grok and refresh DECISIONS via append_sync."""
    clean = [b.strip() for b in bullets if b and b.strip()]
    if not clean:
        clean = ["(no bullets provided)"]

    block = _format_block(topic, clean, next_step=next_step, source=source)
    grok_block = (
        f"\n\n---\n### [{_ts_utc()}] Bridge fix handoff\n"
        f"- **{topic}**\n"
        + "\n".join(f"- {b}" for b in clean)
        + "\n"
    )

    SYNC_DIR.mkdir(parents=True, exist_ok=True)
    inbox_text = CURSOR_INBOX.read_text(encoding="utf-8") if CURSOR_INBOX.exists() else ""
    CURSOR_INBOX.write_text(
        _insert_after_rules_marker(inbox_text, block + _grok_sync_json(topic, clean, next_step)),
        encoding="utf-8",
    )

    if mirror_grok:
        with GROK_OUTBOX.open("a", encoding="utf-8") as fh:
            fh.write(grok_block)

    _append_inbox_jsonl(topic, clean, next_step)

    paste = (
        "## Paste in Cursor chat\n"
        "@DECISIONS.md @sync/cursor_inbox.md @sync/grok_outbox.md\n\n"
        f"Continue SPY bridge. Latest fix handoff: **{topic}** ({_ts_et()}).\n"
    )
    DESKTOP_HANDOFF.write_text(paste + "\n" + block, encoding="utf-8")

    if run_sync:
        run_append_sync()

    return CURSOR_INBOX


def queue_fix(
    topic: str,
    bullets: list[str],
    *,
    next_step: str = "",
    flush_now: bool = True,
) -> None:
    """Queue a fix for handoff; default flushes immediately so Cursor gets it when fixes land."""
    SYNC_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        "topic": topic,
        "bullets": bullets,
        "next": next_step,
    }
    with FIXES_PENDING.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    if flush_now:
        drain_pending_fixes()


def drain_pending_fixes() -> int:
    if not FIXES_PENDING.is_file() or FIXES_PENDING.stat().st_size == 0:
        return 0
    lines = [
        ln.strip()
        for ln in FIXES_PENDING.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    FIXES_PENDING.write_text("", encoding="utf-8")
    for ln in lines:
        data = json.loads(ln)
        handoff(
            data.get("topic", "Queued fix"),
            list(data.get("bullets") or []),
            next_step=data.get("next", ""),
            run_sync=False,
        )
    if lines:
        run_append_sync()
    return len(lines)


def handoff_arm_session(*, gui_team_preserved: bool) -> Path:
    bullets = [
        "ARM for open completed; operator grant on Desktop",
        "9:31 ET burst task SPY-BURST-931-ET registered (weekdays)",
        "Monitor: launchers/MONITOR-AND-READY.bat → WHAT-YOU-DO-NOW.txt",
        (
            "GUI team preserved (no console spawn)"
            if gui_team_preserved
            else "Console command_center.py supervisor spawned"
        ),
    ]
    return handoff("ARM session", bullets, next_step="Confirm TV MACD ENTRY on; Render /health 5.5.9+")


def handoff_monitor_ready_fixes() -> Path:
    """Standing handoff for monitor/READY + dedupe fixes (Jun 2026)."""
    return handoff(
        "Monitor + READY + dedupe",
        [
            "dedupe_spy_workers: keep newest PID (WMI creation time); never taskkill /T",
            "Worker duplicates: kill extras only; GUI START TEAM uses fresh_team=False",
            "ARM: post-spawn dedupe supervisors only; skip console if gui_team_active",
            "command_center: ensure_team_workers each health cycle; GUI reconcile 15s",
            "DEDUPE-SPY-WORKERS.bat: supervisors only — avoid during market",
            "User: GUI *or* ARM/console — not both (prevents duplicate helpers)",
        ],
        next_step="Run MONITOR-AND-READY.bat; green READY = standby OK",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hand off bridge fixes to Cursor via sync/")
    parser.add_argument("--topic", default="Bridge fix")
    parser.add_argument("--bullet", action="append", default=[], dest="bullets")
    parser.add_argument("--next", default="")
    parser.add_argument("--arm", action="store_true", help="ARM session handoff template")
    parser.add_argument("--monitor-ready", action="store_true", help="Ship monitor/READY fix summary")
    parser.add_argument("--drain-pending", action="store_true", help="Flush fixes_pending.jsonl")
    parser.add_argument("--queue-only", action="store_true", help="Append pending without handoff now")
    args = parser.parse_args(argv)

    if args.drain_pending:
        n = drain_pending_fixes()
        print(f"drained {n} pending fix(es)")
        return 0

    if args.monitor_ready:
        path = handoff_monitor_ready_fixes()
        print(f"handoff written → {path}")
        return 0

    if args.arm:
        path = handoff_arm_session(gui_team_preserved=False)
        print(f"handoff written → {path}")
        return 0

    if args.bullets:
        if args.queue_only:
            queue_fix(args.topic, args.bullets, next_step=args.next, flush_now=False)
            print(f"queued → {FIXES_PENDING}")
            return 0
        handoff(args.topic, args.bullets, next_step=args.next)
        print(f"handoff written → {CURSOR_INBOX}")
        return 0

    n = drain_pending_fixes()
    if n:
        print(f"drained {n} pending fix(es) → {CURSOR_INBOX}")
        return 0
    print("usage: --bullet 'fix description' [--topic T] [--next N] | --monitor-ready | --drain-pending")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
