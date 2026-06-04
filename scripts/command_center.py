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
HEALTH_TIMEOUTS = (30.0, 60.0, 90.0)
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


def fetch_health(timeout: float = 60) -> tuple[bool, str]:
    last_err = "unknown"
    waits = HEALTH_TIMEOUTS if timeout <= 30 else (timeout, *HEALTH_TIMEOUTS)
    for wait in waits:
        try:
            req = urllib.request.Request(
                HEALTH_URL,
                headers={"User-Agent": f"{PROJECT_NAME}/1.0"},
            )
            with urllib.request.urlopen(req, timeout=wait) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
                ver = data.get("version", "?")
                configured = data.get("configured", False)
                risk = (data.get("tv_pause_risk") or {}).get("level", "?")
                return True, f"HTTP {resp.status} version={ver} configured={configured} tv_risk={risk}"
        except urllib.error.HTTPError as exc:
            last_err = f"HTTP {exc.code}"
        except Exception as exc:
            last_err = f"FAIL: {type(exc).__name__}"
    return False, last_err


def dedupe_workers() -> None:
    script = SCRIPTS / "dedupe_spy_workers.py"
    if not script.is_file():
        return
    py = python_exe()
    try:
        subprocess.run([str(py), str(script)], cwd=str(ROOT), capture_output=True, timeout=60)
        log_line("dedupe_spy_workers completed")
    except (subprocess.TimeoutExpired, OSError) as exc:
        log_line(f"dedupe_spy_workers skipped: {type(exc).__name__}")


def worker_already_running(script: str) -> bool:
    """Avoid duplicate worker if same script already in a python process."""
    if sys.platform != "win32":
        return False
    needle = script.replace("\\", "/")
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        f"Where-Object {{ $_.CommandLine -match [regex]::Escape('{needle}') }} | "
        "Select-Object -First 1 -ExpandProperty ProcessId"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    pid = (proc.stdout or "").strip()
    return bool(pid and pid.isdigit())


def supervisor_already_running() -> bool:
    """Another command_center supervisor (console or GUI) already active."""
    if sys.platform != "win32":
        return False
    my_pid = os.getpid()
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object {{ $_.CommandLine -match 'command_center(\\.py|_gui\\.py)' "
        f"-and $_.ProcessId -ne {my_pid} }} | "
        "Select-Object -First 1 -ExpandProperty ProcessId"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    pid = (proc.stdout or "").strip()
    return bool(pid and pid.isdigit())


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
        if worker_already_running(script):
            log_line(f"skip {name}: {script} already running")
            continue
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
    if supervisor_already_running():
        log_line("another command_center supervisor already running — exit without spawn")
        print("Command center supervisor already running — not starting a duplicate.")
        return 0
    print_banner()
    LOG_PATH.write_text(
        f"{PROJECT_NAME} session started {datetime.now(ET).isoformat()}\n",
        encoding="utf-8",
    )

    auto_arm = DESKTOP / "OPERATOR-AUTO-ARM.txt"
    if auto_arm.is_file():
        try:
            sys.path.insert(0, str(SCRIPTS))
            import operator_gateway as og  # noqa: E402

            granted = og.try_auto_grant_from_marker()
            if granted:
                log_line(f"auto operator grant: {granted.name}")
        except Exception as exc:
            log_line(f"auto operator grant skipped: {type(exc).__name__}")

    mark_todo_items()
    dedupe_workers()

    for path in OPEN_PATHS:
        open_notepad(path)

    try_open_cursor_workspace()

    procs = spawn_all_workers()
    log_line(
        "auto-run: dual_sync (60s) + bridge_keepalive + redundant_test_loop "
        "(pauses 9:30-16:00 ET; STOP file halts)"
    )
    try:
        sys.path.insert(0, str(SCRIPTS))
        from team_email import notify_command_center_started  # noqa: E402

        if notify_command_center_started():
            log_line("startup email sent")
        else:
            log_line("startup email skipped (SMTP not configured)")
    except Exception as exc:
        log_line(f"startup email error: {type(exc).__name__}")

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
