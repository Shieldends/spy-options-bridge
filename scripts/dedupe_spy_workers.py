#!/usr/bin/env python3
"""Kill duplicate SPY worker python processes; keep newest PID per script."""

from __future__ import annotations

import subprocess
import sys
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


def kill_pid(pid: int) -> bool:
    if sys.platform != "win32":
        return False
    proc = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def main() -> int:
    killed = 0
    for script in TARGETS:
        pids = pids_for_script(script)
        if len(pids) <= 1:
            print(f"{script}: {len(pids)} process(es) - ok")
            continue
        keep = max(pids)
        extras = [p for p in pids if p != keep]
        print(f"{script}: {len(pids)} found - keeping PID {keep}, killing {extras}")
        for pid in extras:
            if kill_pid(pid):
                killed += 1
    print(f"done - killed {killed} duplicate process(es)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
