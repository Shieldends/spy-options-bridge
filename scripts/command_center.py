#!/usr/bin/env python3
"""SPY Live Command Center (console) — bridge + Grok sync + Cursor workspace (stdlib only)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
DESKTOP = Path(r"C:\Users\Shiel\Desktop")
SYNC_DIR = Path(r"C:\Users\Shiel\Projects\spy-hybrid-v3\sync")
HEALTH_URL = "https://spy-options-bridge.onrender.com/health"
LOG_PATH = DESKTOP / "COMMAND-CENTER-LOG.txt"
DEFAULT_STATUS_EMAIL = "shieldinc850@gmail.com"
INTERVAL_SEC = 60
ET = ZoneInfo("America/New_York")
PROJECT_NAME = "SPY Live Command Center"

WORKERS = (
    ("dual_sync", "dual_sync_loop.py", []),
    ("keepalive", "bridge_keepalive.py", []),
    ("redundant_tests", "redundant_test_loop.py", ["--interval", "300"]),
)

OPEN_PATHS = (
    SYNC_DIR / "cursor_inbox.md",
    SYNC_DIR / "grok_outbox.md",
    DESKTOP / "LIVE-RUN-READINESS-REPORT.txt",
)


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = val.strip().strip('"').strip("'")


def status_email() -> str:
    """Status-only recipient from env (never log SMTP secrets)."""
    return (os.getenv("EMAIL_TO") or DEFAULT_STATUS_EMAIL).strip()


def python_exe() -> Path:
    venv_py = ROOT / ".venv" / "Scripts" / "python.exe"
    return venv_py if venv_py.exists() else Path(sys.executable)


def log_line(msg: str) -> None:
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    line = f"{ts} {msg}"
    print(line)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def print_banner() -> None:
    email = status_email()
    print()
    print("=" * 72)
    print("SPY LIVE COMMAND — Bridge + Grok + Cursor")
    print(f"{PROJECT_NAME} | {ROOT}")
    print(f"Status alerts → {email}")
    print("=" * 72)
    print()


def worker_command(script: str, extra_args: list[str]) -> list[str]:
    py = python_exe()
    return [str(py), str(SCRIPTS / script), *extra_args]


def fetch_health(timeout: float = 30) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(
            HEALTH_URL,
            headers={"User-Agent": f"{PROJECT_NAME}/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            ver = data.get("version", "?")
            configured = data.get("configured", False)
            return True, f"HTTP {resp.status} version={ver} configured={configured}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, f"FAIL: {type(exc).__name__}"


def spawn_worker(name: str, script: str, extra_args: list[str]) -> subprocess.Popen[bytes]:
    cmd = worker_command(script, extra_args)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    log_line(f"spawn {name}: {' '.join(Path(c).name if c.endswith('.py') else c for c in cmd)}")
    return subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        creationflags=flags,
    )


def kill_worker(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def open_notepad(path: Path) -> None:
    if not path.exists():
        log_line(f"skip open (missing): {path}")
        return
    subprocess.Popen(
        ["notepad.exe", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    log_line(f"opened: {path.name}")


def try_open_cursor_workspace() -> None:
    workspace = str(ROOT)
    cursor_bin = shutil.which("cursor") or shutil.which("cursor.cmd")
    if cursor_bin:
        subprocess.Popen(
            [cursor_bin, workspace],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log_line("Cursor workspace launch requested")
        return
    print(f"Cursor CLI not found — open manually: {workspace}")
    log_line(f"Cursor CLI missing — path: {workspace}")


def mark_todo_items() -> None:
    py = python_exe()
    for item in ("dual_sync_running", "keepalive_running"):
        subprocess.run(
            [str(py), str(SCRIPTS / "mark_todo_done.py"), "--item", item],
            cwd=str(ROOT),
            capture_output=True,
            timeout=30,
        )


def spawn_all_workers() -> dict[str, subprocess.Popen[bytes]]:
    procs: dict[str, subprocess.Popen[bytes]] = {}
    for name, script, args in WORKERS:
        procs[name] = spawn_worker(name, script, args)
    return procs


def supervise_loop(procs: dict[str, subprocess.Popen[bytes]]) -> None:
    log_line(
        f"supervisor: /health every {INTERVAL_SEC}s → {LOG_PATH.name} "
        f"(Ctrl+C stops all children)"
    )
    while True:
        ok, detail = fetch_health()
        level = "health OK" if ok else "health WARN"
        log_line(f"{level} | {detail} | {HEALTH_URL}")

        for name, proc in procs.items():
            code = proc.poll()
            if code is not None:
                log_line(f"worker exited: {name} code={code}")

        time.sleep(INTERVAL_SEC)


def main() -> int:
    _load_dotenv()
    print_banner()
    LOG_PATH.write_text(
        f"{PROJECT_NAME} session started {datetime.now(ET).isoformat()}\n",
        encoding="utf-8",
    )

    mark_todo_items()

    for path in OPEN_PATHS:
        open_notepad(path)

    try_open_cursor_workspace()

    procs = spawn_all_workers()
    log_line(
        "auto-run: dual_sync (60s) + bridge_keepalive + redundant_test_loop "
        "until 9:30 ET or STOP file"
    )

    try:
        supervise_loop(procs)
    except KeyboardInterrupt:
        log_line("Ctrl+C — stopping workers")
    finally:
        for name, proc in procs.items():
            kill_worker(proc)
            log_line(f"stopped: {name}")
        log_line(f"{PROJECT_NAME} session ended")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
