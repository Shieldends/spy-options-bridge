#!/usr/bin/env python3
"""Print monitor/READY status and refresh Desktop SPY-Command-Center/WHAT-YOU-DO-NOW.txt."""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import command_center as cc  # noqa: E402
import dedupe_spy_workers as dedupe  # noqa: E402

DESKTOP_CC = Path(r"C:\Users\Shiel\Desktop\SPY-Command-Center\WHAT-YOU-DO-NOW.txt")
REPO_NOW = ROOT / "WHAT-YOU-DO-NOW.txt"
ET = ZoneInfo("America/New_York")

CHECKS = (
    ("command_center.py", "command_center.py"),
    ("dual_sync_loop.py", "dual_sync_loop.py"),
    ("bridge_keepalive.py", "bridge_keepalive.py"),
    ("redundant_test_loop.py", "redundant_test_loop.py"),
)


def counts() -> dict[str, int]:
    return {label: len(dedupe.pids_for_script(script)) for label, script in CHECKS}


def ready_line(counts_map: dict[str, int]) -> str:
    dupes = [k for k, v in counts_map.items() if v > 1]
    if dupes:
        return f"WARN — duplicates {', '.join(dupes)} (GUI will reconcile; avoid full DEDUPE bat)"
    core = counts_map["dual_sync_loop.py"] >= 1 and counts_map["bridge_keepalive.py"] >= 1
    if not core:
        return "NOT READY — core helpers down (run ARM or START TEAM)"
    if cc.team_ready_for_display():
        return "READY — monitor team OK (TV → Render → Alpaca)"
    if counts_map["redundant_test_loop.py"] >= 1:
        return "READY — all helpers up"
    return "READY — live session (redundant optional after 9:30 ET)"


def status_block(counts_map: dict[str, int]) -> str:
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
    line = ready_line(counts_map)
    rows = "\n".join(f"  {k:24}: {counts_map[k]}" for k in counts_map)
    return f"MONITOR ({ts})\n{rows}\n  {line}\n"


def write_status_files(block: str) -> None:
    for path in (DESKTOP_CC, REPO_NOW):
        if not path.parent.exists() and path == DESKTOP_CC:
            path.parent.mkdir(parents=True, exist_ok=True)
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if "MONITOR (" in text:
            text = re.sub(
                r"MONITOR \([\s\S]*?(?=\n[A-Z]{2,}|\n={3,}|\Z)",
                block + "\n",
                text,
                count=1,
            )
        elif text.strip():
            text = text.rstrip() + "\n\n" + block
        else:
            text = block
        path.write_text(text, encoding="utf-8")


def main() -> int:
    c = counts()
    block = status_block(c)
    write_status_files(block)
    print(block, end="")
    return 0 if "READY" in ready_line(c) else 1


if __name__ == "__main__":
    raise SystemExit(main())
