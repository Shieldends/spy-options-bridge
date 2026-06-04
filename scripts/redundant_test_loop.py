#!/usr/bin/env python3
"""Redundant pre-market test loop — every N minutes until STOP file or 9:30 ET."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from security_utils import redact_text  # noqa: E402
from run_preopen_matrix import (  # noqa: E402
    market_session_open,
    pre_open_pressure_enabled,
    pre_open_test_aggressive,
)

STOP_FILE = Path(r"C:\Users\Shiel\Desktop\STOP-REDUNDANT-TESTS.txt")
LOG_PATH = Path(r"C:\Users\Shiel\Desktop\REDUNDANT-TEST-LOG.txt")
ET = ZoneInfo("America/New_York")
DEFAULT_INTERVAL = 300


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    import os

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k and k not in os.environ:
            os.environ[k] = v.strip().strip('"').strip("'")


def log_line(msg: str) -> None:
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    line = f"{ts} {msg}"
    print(line)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def should_stop() -> str | None:
    if STOP_FILE.exists():
        return f"STOP file present: {STOP_FILE}"
    now = datetime.now(ET)
    if market_session_open(now):
        return "Market open reached (9:30 AM ET) — live run begins"
    return None


def run_one_cycle(*, fast: bool = True) -> tuple[int, int]:
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(sys.executable)
    proc = subprocess.run(
        [str(py), str(SCRIPTS / "run_preopen_matrix.py"), "--fast", "--append"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    summary = "unknown"
    for ln in out.splitlines():
        if "SUMMARY:" in ln:
            summary = ln.strip()
            break
    status = "PASS" if proc.returncode == 0 else "FAIL"
    log_line(f"CYCLE {status} exit={proc.returncode} {summary}")
    if proc.returncode != 0 and out.strip():
        tail = redact_text("\n".join(out.strip().splitlines()[-2:]), max_len=300)
        log_line(f"detail: {tail}")
    fail_n = 0
    pass_n = 0
    if "FAIL" in summary:
        parts = summary.replace("SUMMARY:", "").strip().split(",")
        for p in parts:
            if "PASS" in p:
                try:
                    pass_n = int(p.strip().split()[0])
                except ValueError:
                    pass
            if "FAIL" in p:
                try:
                    fail_n = int(p.strip().split()[0])
                except ValueError:
                    pass
    if proc.returncode != 0 and fail_n == 0:
        fail_n = 1
    return pass_n, fail_n, summary


def _team_email():
    _load_dotenv()
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from team_email import (  # noqa: E402
        notify_health_fail,
        notify_redundant_cycle,
        try_email_final_report_available,
    )

    return notify_redundant_cycle, notify_health_fail, try_email_final_report_available




def run_burst_pressure_hook() -> None:
    """When pre_open_pressure enabled, log burst path; optional mini-burst via env."""
    if not pre_open_pressure_enabled():
        return
    if not pre_open_test_aggressive():
        return
    log_line(
        "BURST REQUIRED (pre_open_pressure): Desktop BURST-PAPER-100.bat or "
        "burst_paper_fills.py --count 100 (Render /exercise/burst)"
    )
    import os

    if os.environ.get("REDUNDANT_BURST_EACH_CYCLE", "").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(sys.executable)
    log_line("REDUNDANT_BURST_EACH_CYCLE=1 -> burst_paper_fills --count 5 --batch-size 5")
    proc = subprocess.run(
        [
            str(py),
            str(SCRIPTS / "burst_paper_fills.py"),
            "--count",
            "5",
            "--batch-size",
            "5",
            "--interval",
            "2",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=1800,
    )
    tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-2:]
    if tail:
        log_line("burst hook: " + redact_text(" | ".join(tail), max_len=240))
    log_line(f"burst hook exit={proc.returncode}")


def email_cycle_summary(cycle: int, pass_n: int, fail_n: int, summary: str) -> None:
    notify_redundant_cycle, notify_health_fail, try_email_final = _team_email()
    if notify_redundant_cycle(cycle, pass_n, fail_n, summary):
        log_line("Cycle summary email sent")
    else:
        log_line("Cycle summary email skipped (rate limit or SMTP)")
    if fail_n > 0:
        ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
        detail = f"Redundant test FAIL at {ts}. PASS={pass_n} FAIL={fail_n}. {summary}"
        if notify_health_fail(detail):
            log_line("FAIL alert email sent")
        else:
            log_line("FAIL alert email skipped (SMTP not configured)")
    try_email_final()


def main() -> int:
    parser = argparse.ArgumentParser(description="Redundant pre-market test loop")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="Seconds between cycles")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args()

    if STOP_FILE.exists():
        STOP_FILE.unlink(missing_ok=True)
        log_line("Cleared old STOP file — starting fresh")

    log_line(f"redundant_test_loop started interval={args.interval}s (Ctrl+C or STOP file or 9:30 ET to end)")

    cycle = 0
    try:
        while True:
            reason = should_stop()
            if reason:
                log_line(f"STOP: {reason}")
                break

            cycle += 1
            log_line(f"--- cycle {cycle} begin ---")
            pass_n, fail_n, summary = run_one_cycle(fast=True)
            email_cycle_summary(cycle, pass_n, fail_n, summary)

            if args.once:
                break

            reason = should_stop()
            if reason:
                log_line(f"STOP: {reason}")
                break

            log_line(f"sleep {args.interval}s (create STOP file to halt early)")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log_line("STOP: Ctrl+C")

    log_line("redundant_test_loop ended")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
