#!/usr/bin/env python3
"""SPY Live Command Center (console) — bridge + Grok sync + Cursor workspace (stdlib only)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import traceback
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
GUI_LOCK = DESKTOP / "SPY-CC-GUI.lock"
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


def _configure_stdio_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def _safe_print(text: str, **kwargs: object) -> None:
    try:
        print(text, **kwargs)
    except (UnicodeEncodeError, OSError):
        safe = text.encode("ascii", errors="replace").decode("ascii")
        try:
            print(safe, **kwargs)
        except (UnicodeEncodeError, OSError):
            pass


def _utf8_child_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def python_exe() -> Path:
    venv_py = ROOT / ".venv" / "Scripts" / "python.exe"
    return venv_py if venv_py.exists() else Path(sys.executable)


def log_line(msg: str) -> None:
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    line = f"{ts} {msg}"
    _safe_print(line)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def print_banner() -> None:
    email = status_email()
    _safe_print("")
    _safe_print("=" * 72)
    _safe_print("SPY LIVE COMMAND - Bridge + Grok + Cursor")
    _safe_print(f"{PROJECT_NAME} | {ROOT}")
    _safe_print(f"Status alerts -> {email}")
    _safe_print("=" * 72)
    _safe_print("")


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


def dedupe_workers(*, reset: bool = False) -> None:
    """Remove duplicate processes. reset=True kills all workers (STOP ALL / fresh console only)."""
    script = SCRIPTS / "dedupe_spy_workers.py"
    if not script.is_file():
        return
    py = python_exe()
    cmd = [str(py), str(script)]
    if reset:
        cmd.append("--reset-workers")
    try:
        subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        log_line("dedupe_spy_workers completed" + (" (reset)" if reset else ""))
    except (subprocess.TimeoutExpired, OSError) as exc:
        log_line(f"dedupe_spy_workers skipped: {type(exc).__name__}")
    except Exception as exc:
        log_line(f"dedupe_spy_workers error: {type(exc).__name__}: {exc}")


def _win_python_process_where(commandline_match: str, *, exclude_pid: int | None = None) -> str:
    """Match python.exe and pythonw.exe (Command Center GUI uses pythonw)."""
    exclude = (
        f" -and $_.ProcessId -ne {int(exclude_pid)}"
        if exclude_pid is not None
        else ""
    )
    return (
        "Get-CimInstance Win32_Process | "
        "Where-Object { ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') "
        f"-and $_.CommandLine -match {commandline_match}{exclude} }} | "
        "Select-Object -First 1 -ExpandProperty ProcessId"
    )


def worker_already_running(script: str) -> bool:
    """Avoid duplicate worker if same script already in a python process."""
    if sys.platform != "win32":
        return False
    needle = script.replace("\\", "/").replace("'", "''")
    ps = _win_python_process_where(f"[regex]::Escape('{needle}')")
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


TEAM_WORKER_SCRIPTS = (
    "dual_sync_loop.py",
    "bridge_keepalive.py",
    "redundant_test_loop.py",
)


def _this_process_is_gui() -> bool:
    cmd = " ".join(sys.argv).replace("\\", "/")
    return "command_center_gui.py" in cmd


def gui_supervisor_running() -> bool:
    """SPY Live Command Center GUI (tkinter) is up — excludes self during GUI startup."""
    if other_gui_supervisor_pids():
        return True
    if _this_process_is_gui():
        return False
    return worker_already_running("command_center_gui.py")


def team_workers_running() -> bool:
    """All three START TEAM workers have a python process."""
    return all(worker_already_running(script) for script in TEAM_WORKER_SCRIPTS)


def _market_session_open_now() -> bool:
    try:
        from run_preopen_matrix import market_session_open  # noqa: E402

        return market_session_open()
    except Exception:
        return False


def team_ready_for_display() -> bool:
    """READY banner: core helpers up; redundant optional during live market hours."""
    status = team_worker_status()
    core = status.get("dual_sync_loop.py", False) and status.get("bridge_keepalive.py", False)
    if not core:
        return False
    if status.get("redundant_test_loop.py", False):
        return True
    if _market_session_open_now():
        return True
    return False


def redundant_expected_up() -> bool:
    """Pre-open redundant loop should be running (not during RTH, not when STOP file set)."""
    if _market_session_open_now():
        return False
    stop = Path(r"C:\Users\Shiel\Desktop\STOP-REDUNDANT-TESTS.txt")
    return not stop.exists()


def clear_redundant_stop_file() -> bool:
    """Remove STOP file so redundant_test_loop can run. Returns True if removed."""
    stop = Path(r"C:\Users\Shiel\Desktop\STOP-REDUNDANT-TESTS.txt")
    if not stop.exists():
        return False
    backup = stop.parent / f"STOP-REDUNDANT-TESTS.bak.{datetime.now(ET).strftime('%Y%m%d-%H%M%S')}"
    try:
        stop.rename(backup)
    except OSError:
        stop.unlink(missing_ok=True)
    log_line(f"cleared redundant STOP → {backup.name}")
    return True


def gui_team_active() -> bool:
    """GUI is open and START TEAM workers are all running."""
    return gui_supervisor_running() and team_workers_running()


def arm_should_skip_supervisor_ops() -> bool:
    """ARM skips console spawn only when the GUI already has a full START TEAM running."""
    return gui_team_active()


def team_worker_status() -> dict[str, bool]:
    """Per-script worker presence (any python process with that script in cmdline)."""
    return {script: worker_already_running(script) for script in TEAM_WORKER_SCRIPTS}


def dedupe_worker_duplicates_only() -> None:
    """Kill extra dual_sync/keepalive/redundant PIDs only — never wipe the whole team."""
    script = SCRIPTS / "dedupe_spy_workers.py"
    if not script.is_file():
        return
    py = python_exe()
    for worker_script in TEAM_WORKER_SCRIPTS:
        try:
            subprocess.run(
                [str(py), str(script), "--only", worker_script],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass


def stop_team_workers() -> None:
    """Stop all START TEAM worker processes (GUI STOP ALL or full reset)."""
    dedupe_workers(reset=True)
    log_line("stop_team_workers: reset-workers completed")


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return str(pid) in (proc.stdout or "")
        except (subprocess.TimeoutExpired, OSError):
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def try_acquire_gui_lock() -> bool:
    """One Command Center window per machine — file lock + live PID check."""
    my_pid = os.getpid()
    if GUI_LOCK.is_file():
        try:
            holder = int(GUI_LOCK.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            holder = 0
        if holder and holder != my_pid and _pid_alive(holder):
            return False
    try:
        GUI_LOCK.write_text(str(my_pid), encoding="utf-8")
    except OSError:
        return True
    return True


def release_gui_lock() -> None:
    if not GUI_LOCK.is_file():
        return
    try:
        if int(GUI_LOCK.read_text(encoding="utf-8").strip()) == os.getpid():
            GUI_LOCK.unlink(missing_ok=True)  # type: ignore[arg-type]
    except (ValueError, OSError):
        pass


def consolidate_to_one_gui() -> int:
    """Leave one GUI process (highest PID = newest); kill extras."""
    if sys.platform != "win32":
        return 0
    try:
        import dedupe_spy_workers as dedupe  # noqa: E402
    except Exception:
        return 0
    pids = dedupe.pids_for_script("command_center_gui.py")
    if len(pids) <= 1:
        return len(pids)
    keep = max(pids)
    killed = 0
    for pid in pids:
        if pid == keep:
            continue
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            check=False,
        )
        killed += 1
    if killed:
        log_line(f"consolidate_to_one_gui: kept PID {keep}, ended {killed} extra GUI(s)")
    try:
        GUI_LOCK.write_text(str(keep), encoding="utf-8")
    except OSError:
        pass
    return 1


def other_gui_supervisor_pids() -> list[int]:
    """GUI PIDs other than this process (never treat self as a duplicate to kill)."""
    if sys.platform != "win32":
        return []
    try:
        import dedupe_spy_workers as dedupe  # noqa: E402
    except Exception:
        return []
    my_pid = os.getpid()
    return [p for p in dedupe.pids_for_script("command_center_gui.py") if p != my_pid]


def kill_gui_supervisor_processes() -> int:
    """End hidden/stale GUI processes so double-click can show a visible window."""
    if sys.platform != "win32":
        return 0
    killed = 0
    for pid in other_gui_supervisor_pids():
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            check=False,
        )
        killed += 1
    if killed:
        log_line(f"kill_gui_supervisor_processes: ended {killed} stale GUI process(es)")
    return killed


def stop_stale_console_supervisors() -> int:
    """When GUI is up, end hidden command_center.py loops that fight the GUI team."""
    if sys.platform != "win32" or not gui_supervisor_running():
        return 0
    try:
        import dedupe_spy_workers as dedupe  # noqa: E402
    except Exception:
        return 0
    pids = dedupe.pids_for_script("command_center.py")
    killed = 0
    for pid in pids:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            check=False,
        )
        killed += 1
    if killed:
        log_line(f"stop_stale_console_supervisors: ended {killed} console supervisor(s)")
    return killed


def supervisor_already_running() -> bool:
    """Another command_center supervisor (console or GUI) already active."""
    if sys.platform != "win32":
        return False
    my_pid = os.getpid()
    ps = _win_python_process_where(
        "'command_center(\\.py|_gui\\.py)'",
        exclude_pid=my_pid,
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


def _worker_creationflags() -> int:
    if sys.platform != "win32":
        return 0
    flags = subprocess.CREATE_NEW_PROCESS_GROUP
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return flags | breakaway | no_window


def spawn_worker(name: str, script: str, extra_args: list[str]) -> subprocess.Popen[bytes]:
    cmd = worker_command(script, extra_args)
    flags = _worker_creationflags()
    log_line(f"spawn {name}: {' '.join(Path(c).name if c.endswith('.py') else c for c in cmd)}")
    return subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        creationflags=flags,
        env=_utf8_child_env(),
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
    _safe_print(f"Cursor CLI not found - open manually: {workspace}")
    log_line(f"Cursor CLI missing — path: {workspace}")


def mark_todo_items() -> None:
    py = python_exe()
    for item in ("dual_sync_running", "keepalive_running"):
        subprocess.run(
            [str(py), str(SCRIPTS / "mark_todo_done.py"), "--item", item],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )


def should_spawn_worker(name: str, script: str) -> bool:
    """Redundant pre-open loop must not respawn during live market hours."""
    if script == "redundant_test_loop.py" and not redundant_expected_up():
        return False
    return True


def spawn_all_workers(*, fresh_team: bool = False) -> dict[str, subprocess.Popen[bytes]]:
    procs: dict[str, subprocess.Popen[bytes]] = {}
    for name, script, args in WORKERS:
        if not should_spawn_worker(name, script):
            log_line(f"skip {name}: paused for live session (9:30–16:00 ET or STOP file)")
            continue
        if not fresh_team and worker_already_running(script):
            log_line(f"skip {name}: {script} already running")
            continue
        procs[name] = spawn_worker(name, script, args)
    return procs


def ensure_team_workers(procs: dict[str, subprocess.Popen[bytes]]) -> None:
    """Restart tracked workers; spawn any helper that is down."""
    if gui_supervisor_running():
        return
    for name, script, args in WORKERS:
        if not should_spawn_worker(name, script):
            continue
        proc = procs.get(name)
        if proc is not None and proc.poll() is None:
            continue
        if proc is not None and proc.poll() is not None:
            exit_code = proc.poll()
            if worker_already_running(script):
                log_line(
                    f"{name}: tracked child exited code={exit_code} — "
                    "untracked helper still running (skip respawn)"
                )
                procs.pop(name, None)
                continue
            log_line(f"worker exited: {name} code={exit_code} — restarting")
        elif worker_already_running(script):
            log_line(f"{name}: helper already running (untracked) — ok")
            continue
        else:
            log_line(f"{name}: helper down — starting")
        procs[name] = spawn_worker(name, script, args)


def supervise_loop(procs: dict[str, subprocess.Popen[bytes]]) -> None:
    log_line(
        f"supervisor: /health every {INTERVAL_SEC}s -> {LOG_PATH.name} "
        f"(Ctrl+C stops all children)"
    )
    while True:
        ok, detail = fetch_health()
        level = "health OK" if ok else "health WARN"
        log_line(f"{level} | {detail} | {HEALTH_URL}")
        ensure_team_workers(procs)
        time.sleep(INTERVAL_SEC)


def main() -> int:
    _load_dotenv()
    _configure_stdio_utf8()
    if gui_supervisor_running():
        log_line("GUI Command Center open — console supervisor not started (avoids killing team)")
        _safe_print("Command Center GUI is already open. Use that window; console supervisor skipped.")
        return 0
    if supervisor_already_running():
        log_line("another command_center supervisor already running — exit without spawn")
        _safe_print("Command center supervisor already running - not starting a duplicate.")
        return 0
    print_banner()
    log_line(f"{PROJECT_NAME} session started pid={os.getpid()}")

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
    dedupe_worker_duplicates_only()

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
    try:
        raise SystemExit(main())
    except SystemExit as exc:
        if exc.code not in (0, None):
            crash = DESKTOP / "COMMAND-CENTER-CRASH.txt"
            try:
                crash.write_text(f"SystemExit code={exc.code}\n", encoding="utf-8")
            except OSError:
                pass
        raise
    except Exception:
        crash = DESKTOP / "COMMAND-CENTER-CRASH.txt"
        try:
            crash.write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        raise SystemExit(1) from None
