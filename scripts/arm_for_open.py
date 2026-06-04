#!/usr/bin/env python3
"""One-shot arm for market open — grant operator session, start supervisor, schedule burst."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import command_center as cc  # noqa: E402
import operator_gateway as og  # noqa: E402
from cursor_handoff import handoff_arm_session  # noqa: E402

DESKTOP = Path(r"C:\Users\Shiel\Desktop")
SYNC_DIR = Path(r"C:\Users\Shiel\Projects\spy-hybrid-v3\sync")
LOG_PATH = DESKTOP / "ARM-FOR-OPEN.log"
STOP_FILE = DESKTOP / "STOP-REDUNDANT-TESTS.txt"
AUTO_ARM_MARKER = DESKTOP / "OPERATOR-AUTO-ARM.txt"
BURST_PS1 = ROOT / "launchers" / "ARM-SCHEDULE-BURST-931.ps1"
ET = ZoneInfo("America/New_York")
SCHTASK_NAME = "SPY-BURST-931-ET"


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))


def log(msg: str) -> None:
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    line = f"{ts} {msg}"
    _safe_print(line)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def python_exe() -> Path:
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    return venv if venv.exists() else Path(sys.executable)


def _utf8_child_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def confirm_arm(*, yes: bool, countdown_sec: int = 5) -> bool:
    if yes:
        log("confirm: --yes (non-interactive)")
        return True
    _safe_print("")
    _safe_print("ARM FOR OPEN - minimal mode")
    _safe_print(f"  - Session operator grant ({og.load_config().get('arm_grant_hours', 12)}h)")
    _safe_print("  - Command center supervisor (if not running; skipped when GUI is open)")
    _safe_print("  - Clear STOP-REDUNDANT-TESTS.txt")
    _safe_print("  - 9:31 ET burst task (weekdays)")
    _safe_print("")
    if countdown_sec > 0:
        _safe_print(f"Press N to cancel - auto-YES in {countdown_sec}s...")
        for remaining in range(countdown_sec, 0, -1):
            _safe_print(f"  {remaining}...", end="\r", flush=True)
            time.sleep(1)
        _safe_print("  GO     ")
        log("confirm: countdown auto-YES")
        return True
    answer = input("Arm for open? (Y/N): ").strip().upper()
    ok = answer in ("Y", "YES", "")
    log(f"confirm: user {'Y' if ok else 'N'}")
    return ok


def write_arm_grant(cfg: dict) -> Path:
    path = og.write_grant("session", cfg, source="arm-for-open")
    hours = cfg.get("arm_grant_hours", 12)
    log(f"grant written: {path} tier=session hours={hours}")
    return path


def clear_stop_file() -> None:
    if not STOP_FILE.exists():
        log("STOP file absent — ok")
        return
    backup = DESKTOP / f"STOP-REDUNDANT-TESTS.bak.{datetime.now(ET).strftime('%Y%m%d-%H%M%S')}"
    STOP_FILE.rename(backup)
    log(f"STOP file renamed → {backup.name}")


def command_center_running() -> bool:
    if sys.platform != "win32":
        return False
    script = SCRIPTS / "command_center.py"
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        f"Where-Object {{ $_.CommandLine -match 'command_center(\\.py|_gui\\.py)' }} | "
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


def _supervisor_pids() -> list[int]:
    if sys.platform != "win32":
        return []
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -match 'command_center(\\.py|_gui\\.py)' } | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    out: list[int] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            out.append(int(line))
    return sorted(set(out))


def run_dedupe(
    *,
    exclude_pids: list[int] | None = None,
    only: list[str] | None = None,
) -> None:
    py = python_exe()
    dedupe = SCRIPTS / "dedupe_spy_workers.py"
    if not dedupe.is_file():
        return
    cmd = [str(py), str(dedupe)]
    for script in only or []:
        cmd.extend(["--only", script])
    for pid in exclude_pids or []:
        cmd.extend(["--exclude-pid", str(pid)])
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
        log("dedupe_spy_workers completed")
    except (subprocess.TimeoutExpired, OSError) as exc:
        log(f"dedupe_spy_workers skipped: {type(exc).__name__}")


def spawn_command_center() -> bool:
    """Start console supervisor unless GUI Command Center is managing workers. Returns True if skipped."""
    if cc.arm_should_skip_supervisor_ops():
        log("GUI open — skip console spawn (use the open Command Center window)")
        return True
    if cc.team_ready_for_display():
        log("Team already on standby — skip console spawn (open SPY-LIVE-COMMAND.bat only if you want the window)")
        return True
    if cc.team_workers_running() and cc.command_center_running():
        log("console supervisor + helpers already running — skip spawn")
        return False
    run_dedupe(exclude_pids=[], only=["command_center.py", "command_center_gui.py"])
    if command_center_running():
        log("command_center already running - skip spawn")
        return False
    time.sleep(2)
    if command_center_running():
        log("command_center already running (after wait) - skip spawn")
        return False
    py = python_exe()
    cmd = [str(py), str(SCRIPTS / "command_center.py")]
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(cmd, cwd=str(ROOT), creationflags=flags, env=_utf8_child_env())
    log("spawned command_center.py supervisor")
    time.sleep(6)
    keep = _supervisor_pids()
    if keep:
        run_dedupe(
            exclude_pids=[max(keep)],
            only=["command_center.py", "command_center_gui.py"],
        )
        log(f"post-spawn dedupe kept supervisor PID {max(keep)}")
    return False


def register_burst_task(*, skip_schtask: bool = False) -> None:
    if not BURST_PS1.is_file():
        log(f"WARN: missing {BURST_PS1}")
        return
    if skip_schtask:
        log(f"burst script ready: {BURST_PS1} (schtask skipped)")
        return
    ps = (
        f"$name = '{SCHTASK_NAME}'; "
        f"$script = '{BURST_PS1}'; "
        "Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue; "
        "$action = New-ScheduledTaskAction -Execute 'powershell.exe' "
        "-Argument ('-NoProfile -ExecutionPolicy Bypass -File \"' + $script + '\"'); "
        "$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 9:31AM; "
        "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; "
        "Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings "
        "-Description 'SPY paper burst 9:31 ET weekdays' | Out-Null; "
        "Write-Output 'registered'"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log(f"schtask WARN: {type(exc).__name__}")
        return
    out = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode == 0:
        log(f"schtask {SCHTASK_NAME} registered (9:31 ET weekdays)")
    else:
        log(f"schtask WARN exit={proc.returncode}: {out[:200]}")


def append_sync_brief(*, gui_team_preserved: bool = False) -> None:
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
    outbox = SYNC_DIR / "grok_outbox.md"
    outbox.parent.mkdir(parents=True, exist_ok=True)
    supervisor_line = (
        "- GUI Command Center team preserved (ARM skipped dedupe/console spawn)\n"
        if gui_team_preserved
        else "- Command center supervisor running\n"
    )
    block = (
        f"\n\n---\n## {ts} — ARMED minimal mode\n"
        "- Operator session grant (market day)\n"
        f"{supervisor_line}"
        "- Redundant tests cleared; burst 9:31 ET scheduled\n"
        "- Revoke operator: delete Desktop\\OPERATOR-GRANT.json\n"
        "- Full stop: Command Center STOP ALL\n"
    )
    with outbox.open("a", encoding="utf-8") as fh:
        fh.write(block)
    log(f"sync brief appended → {outbox.name}")


def touch_auto_arm_marker() -> None:
    if AUTO_ARM_MARKER.exists():
        log("OPERATOR-AUTO-ARM.txt already present")
        return
    AUTO_ARM_MARKER.write_text(
        "Marker: allow auto operator grant on START TEAM (see OPERATOR-QUICK-START.txt)\n",
        encoding="utf-8",
    )
    log(f"created {AUTO_ARM_MARKER.name}")


def arm(*, yes: bool = False, skip_schtask: bool = False, create_marker: bool = False) -> int:
    cfg = og.load_config()
    if not confirm_arm(yes=yes):
        log("cancelled")
        return 1

    write_arm_grant(cfg)
    clear_stop_file()
    gui_preserved = spawn_command_center()
    register_burst_task(skip_schtask=skip_schtask)
    append_sync_brief(gui_team_preserved=gui_preserved)
    try:
        handoff_arm_session(gui_team_preserved=gui_preserved)
        log("cursor handoff written → sync/cursor_inbox.md")
    except Exception as exc:
        log(f"cursor handoff skipped: {type(exc).__name__}")
    if create_marker:
        touch_auto_arm_marker()

    log("ARM complete — minimal mode until STOP ALL")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Arm for market open (one shot)")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip prompt (non-interactive)")
    parser.add_argument(
        "--skip-schtask",
        action="store_true",
        help="Do not register Windows scheduled task (burst ps1 still on disk)",
    )
    parser.add_argument(
        "--create-auto-arm-marker",
        action="store_true",
        help="Create Desktop OPERATOR-AUTO-ARM.txt for START TEAM auto-grant",
    )
    args = parser.parse_args(argv)
    return arm(yes=args.yes, skip_schtask=args.skip_schtask, create_marker=args.create_auto_arm_marker)


if __name__ == "__main__":
    raise SystemExit(main())
