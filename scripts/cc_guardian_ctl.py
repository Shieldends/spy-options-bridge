#!/usr/bin/env python3
"""Control Live Session Guardian — single supervisor for Command Center 2.0."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
DESKTOP = Path(r"C:\Users\Shiel\Desktop")
GUARDIAN_PID = DESKTOP / "SPY-GUARDIAN.pid"
GUARDIAN_SCRIPT = SCRIPTS / "live_session_guardian.py"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    proc = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return str(pid) in (proc.stdout or "")


def read_guardian_pid() -> int | None:
    if not GUARDIAN_PID.is_file():
        return None
    try:
        return int(GUARDIAN_PID.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def write_guardian_pid(pid: int) -> None:
    DESKTOP.mkdir(parents=True, exist_ok=True)
    GUARDIAN_PID.write_text(str(pid), encoding="utf-8")


def clear_guardian_pid() -> None:
    if GUARDIAN_PID.is_file():
        GUARDIAN_PID.unlink(missing_ok=True)  # type: ignore[arg-type]


def guardian_running() -> bool:
    pid = read_guardian_pid()
    if pid and _pid_alive(pid):
        return True
    if pid:
        clear_guardian_pid()
    return False


def pythonw_exe() -> Path:
    py = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if py.is_file():
        return py
    return Path(sys.executable)


def start_guardian(*, detached: bool = True) -> tuple[bool, str]:
    """Start headless guardian loop. Returns (ok, message)."""
    if guardian_running():
        return True, f"Guardian already running (PID {read_guardian_pid()})"
    if not GUARDIAN_SCRIPT.is_file():
        return False, f"Missing {GUARDIAN_SCRIPT}"
    py = pythonw_exe()
    flags = 0
    if sys.platform == "win32" and detached:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0x08000000
        )
    proc = subprocess.Popen(
        [str(py), str(GUARDIAN_SCRIPT)],
        cwd=str(ROOT),
        creationflags=flags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    write_guardian_pid(proc.pid)
    return True, f"Guardian started PID {proc.pid}"


def stop_guardian() -> tuple[bool, str]:
    pid = read_guardian_pid()
    if not pid:
        return True, "Guardian not running"
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    clear_guardian_pid()
    return True, f"Guardian stopped (was PID {pid})"


def status_line() -> str:
    if guardian_running():
        return f"Guardian ON (PID {read_guardian_pid()})"
    return "Guardian OFF — click Start Guardian in Actions tab"


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Live Session Guardian control")
    parser.add_argument("cmd", choices=("start", "stop", "status"), nargs="?")
    args = parser.parse_args(argv)
    cmd = args.cmd or "status"
    if cmd == "start":
        ok, msg = start_guardian()
    elif cmd == "stop":
        ok, msg = stop_guardian()
    else:
        ok, msg = True, status_line()
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
