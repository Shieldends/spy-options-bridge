#!/usr/bin/env python3
"""Minute heartbeat: watch Grok/Cursor sync files, run append_sync.py every cycle."""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SYNC_DIR = Path(r"C:\Users\Shiel\Projects\spy-hybrid-v3\sync")
APPEND_SYNC = Path(r"C:\Users\Shiel\Projects\spy-hybrid-v3\scripts\append_sync.py")
LOG_PATH = Path(r"C:\Users\Shiel\Desktop\DUAL-SYNC-LOG.txt")
WATCH_FILES = ("cursor_inbox.md", "grok_outbox.md")
INTERVAL_SEC = 60
ET = ZoneInfo("America/New_York")


def log(msg: str) -> None:
    line = f"{datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S ET')} {msg}"
    print(line)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def file_summary(name: str) -> str:
    path = SYNC_DIR / name
    if not path.exists():
        return f"{name}=missing"
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = len(text.splitlines())
    return f"{name} bytes={path.stat().st_size} lines={lines}"


def run_append_sync() -> int:
    if not APPEND_SYNC.exists():
        log(f"ERROR append_sync missing: {APPEND_SYNC}")
        return 1
    proc = subprocess.run(
        [sys.executable, str(APPEND_SYNC)],
        cwd=str(APPEND_SYNC.parents[1]),
        capture_output=True,
        text=True,
    )
    if proc.stdout.strip():
        log(proc.stdout.strip())
    if proc.returncode != 0:
        log(f"append_sync exit {proc.returncode}: {proc.stderr.strip()}")
    else:
        log("append_sync OK")
    return proc.returncode


def heartbeat_cycle(prev_mtime: dict[str, float]) -> dict[str, float]:
    cur_mtime: dict[str, float] = {}
    parts: list[str] = []
    for name in WATCH_FILES:
        path = SYNC_DIR / name
        cur_mtime[name] = path.stat().st_mtime if path.exists() else 0.0
        parts.append(file_summary(name))
    changed = [n for n in WATCH_FILES if cur_mtime.get(n, 0) != prev_mtime.get(n, 0)]
    change_note = f" changed={','.join(changed)}" if changed else ""
    log(f"HEARTBEAT | {' | '.join(parts)}{change_note}")
    run_append_sync()
    return cur_mtime


def main() -> int:
    log(
        "dual_sync_loop started — HEARTBEAT every 60s; "
        "Grok appends grok_outbox; Cursor reads grok_outbox each cycle"
    )
    prev = {name: 0.0 for name in WATCH_FILES}
    while True:
        prev = heartbeat_cycle(prev)
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("dual_sync_loop stopped")
        raise SystemExit(0)
