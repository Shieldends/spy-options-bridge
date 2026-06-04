#!/usr/bin/env python3
"""Kill duplicate SPY worker python processes; keep newest PID per script."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent

TARGETS = (
    "command_center.py",
    "command_center_gui.py",
    "dual_sync_loop.py",
    "bridge_keepalive.py",
    "redundant_test_loop.py",
)

# Never use /T: supervisors spawn workers as children; killing a tree drops the team.
_USE_TREE_KILL = False


def pids_for_script(script: str) -> list[int]:
    if sys.platform != "win32":
        return []
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        f"Where-Object {{ $_.CommandLine -match [regex]::Escape('{script}') }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    out: list[int] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            out.append(int(line))
    return sorted(set(out))


def kill_pid(pid: int, *, kill_tree: bool) -> bool:
    if sys.platform != "win32":
        return False
    args = ["taskkill", "/PID", str(pid), "/F"]
    if kill_tree:
        args = ["taskkill", "/PID", str(pid), "/T", "/F"]
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


_WMI_TS = re.compile(
    r"^(?P<y>\d{4})(?P<m>\d{2})(?P<d>\d{2})(?P<h>\d{2})(?P<mi>\d{2})(?P<s>\d{2})"
)


def process_creation_ts(pid: int) -> float:
    """Sortable start time for duplicate resolution (newest wins)."""
    if sys.platform != "win32":
        return float(pid)
    ps = (
        f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CreationDate"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return 0.0
    raw = (proc.stdout or "").strip()
    m = _WMI_TS.match(raw)
    if not m:
        return 0.0
    dt = datetime(
        int(m["y"]),
        int(m["m"]),
        int(m["d"]),
        int(m["h"]),
        int(m["mi"]),
        int(m["s"]),
        tzinfo=timezone.utc,
    )
    return dt.timestamp()


def choose_keep(pids: list[int], exclude: set[int], script: str) -> int:
    del script  # kept for CLI stability; resolution uses creation time only
    protected = [p for p in pids if p in exclude]
    if protected:
        return max(protected, key=process_creation_ts)
    return max(pids, key=process_creation_ts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kill duplicate SPY worker python processes")
    parser.add_argument(
        "--exclude-pid",
        type=int,
        action="append",
        default=[],
        help="Never kill these PIDs (e.g. current command_center supervisor)",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="SCRIPT",
        help="Limit to script basename(s), e.g. command_center.py",
    )
    parser.add_argument(
        "--reset-workers",
        action="store_true",
        help="Kill every matching worker process (used before supervisor spawns a fresh team)",
    )
    args = parser.parse_args(argv)
    exclude = set(args.exclude_pid or [])
    only = {s.strip() for s in (args.only or []) if s.strip()}
    worker_scripts = {
        "dual_sync_loop.py",
        "bridge_keepalive.py",
        "redundant_test_loop.py",
    }
    if args.reset_workers:
        only = only or worker_scripts
    targets = tuple(t for t in TARGETS if not only or t in only)

    killed = 0
    for script in targets:
        pids = pids_for_script(script)
        if args.reset_workers and script in worker_scripts:
            if not pids:
                print(f"{script}: 0 process(es) - ok")
                continue
            print(f"{script}: reset — killing {pids}")
            for pid in pids:
                if kill_pid(pid, kill_tree=_USE_TREE_KILL):
                    killed += 1
            continue
        if len(pids) <= 1:
            print(f"{script}: {len(pids)} process(es) - ok")
            continue
        keep = choose_keep(pids, exclude, script)
        extras = [p for p in pids if p != keep]
        print(f"{script}: {len(pids)} found - keeping PID {keep}, killing {extras}")
        for pid in extras:
            if kill_pid(pid, kill_tree=_USE_TREE_KILL):
                killed += 1
    print(f"done - killed {killed} duplicate process(es)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
