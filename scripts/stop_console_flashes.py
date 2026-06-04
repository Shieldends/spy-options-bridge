#!/usr/bin/env python3
"""Stop blue/black console flashes — end console supervisor + dedupe workers."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import command_center as cc  # noqa: E402
import cc_guardian_ctl as gctl  # noqa: E402

DESKTOP = Path(r"C:\Users\Shiel\Desktop")
LOG = DESKTOP / "STOP-CONSOLE-FLASHES.txt"
ET = ZoneInfo("America/New_York")


def main() -> int:
    lines: list[str] = []
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    lines.append(f"STOP CONSOLE FLASHES | {ts}")

    n = cc.stop_stale_console_supervisors()
    lines.append(f"console supervisors ended: {n}")

    try:
        import dedupe_spy_workers as dedupe  # noqa: E402

        before = {s: len(dedupe.pids_for_script(s)) for s in cc.TEAM_WORKER_SCRIPTS}
        lines.append(f"workers before dedupe: {before}")
    except Exception as exc:
        lines.append(f"worker count skip: {type(exc).__name__}")

    cc.dedupe_worker_duplicates_only()
    lines.append("dedupe pass complete (hidden subprocess)")

    try:
        import dedupe_spy_workers as dedupe  # noqa: E402

        after = {s: len(dedupe.pids_for_script(s)) for s in cc.TEAM_WORKER_SCRIPTS}
        lines.append(f"workers after dedupe: {after}")
    except Exception as exc:
        lines.append(f"worker count after skip: {type(exc).__name__}")

    lines.append(f"guardian: {gctl.status_line()}")
    lines.append("OK — use pythonw Guardian + Command Center 2.0 Team tab")
    lines.append("Avoid: ARM bat + GUI together, MONITOR bat in a loop")

    text = "\n".join(lines) + "\n"
    LOG.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
