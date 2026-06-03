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
    if now.weekday() < 5:
        open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
        if now >= open_time:
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
    return pass_n, fail_n


def email_on_fail(pass_n: int, fail_n: int) -> None:
    if fail_n == 0:
        return
    _load_dotenv()
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from team_email import notify_health_fail  # noqa: E402

    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    detail = (
        f"Redundant test FAIL at {ts}. PASS={pass_n} FAIL={fail_n}. "
        f"Log: {LOG_PATH}. Results: PRE-OPEN-TEST-RESULTS.txt on Desktop."
    )
    if notify_health_fail(detail):
        log_line("FAIL email sent to EMAIL_TO")
    else:
        log_line("FAIL email skipped (SMTP not configured)")


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
            pass_n, fail_n = run_one_cycle(fast=True)
            if fail_n > 0:
                email_on_fail(pass_n, fail_n)

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
