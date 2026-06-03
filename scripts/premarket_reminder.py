#!/usr/bin/env python3
"""Pre-market incomplete-task reminder — email + Desktop REMINDER-BEFORE-OPEN.txt."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

import todo_checklist as tc  # noqa: E402
from team_email import notify_premarket_reminder  # noqa: E402

ET = tc.ET
CHECKLIST_PATH = tc.CHECKLIST_PATH
before_market_open = tc.before_market_open
format_incomplete_lines = tc.format_incomplete_lines
incomplete_items = tc.incomplete_items
is_weekday_market_day = tc.is_weekday_market_day

REMINDER_FILE = Path(r"C:\Users\Shiel\Desktop\REMINDER-BEFORE-OPEN.txt")
DEFAULT_TO = "shieldinc850@gmail.com"


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k and k not in __import__("os").environ:
            __import__("os").environ[k] = v.strip().strip('"').strip("'")


def print_status(quick: bool = False) -> int:
    missing = incomplete_items()
    now = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    if not missing:
        print(f"[{now}] All pre-market checklist items are DONE.")
        return 0
    print(f"[{now}] INCOMPLETE pre-market items ({len(missing)}):")
    for line in format_incomplete_lines():
        print(line)
    if quick:
        print("\nRun SETUP-EMAIL-AUTOMATION.bat first if email is not configured.")
    return 1 if missing else 0


def write_reminder_file(lines: list[str]) -> None:
    now = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    body = [
        "SPY BRIDGE — REMINDER BEFORE MARKET OPEN (9:30 AM ET)",
        f"Generated: {now}",
        "No secrets in this file.",
        "",
        "Incomplete checklist items:",
        *lines,
        "",
        "Suggested order tonight / before open:",
        "  1. SETUP-EMAIL-AUTOMATION.bat",
        "  2. CONFIRM-RENDER-EMAIL.bat (after Render env saved)",
        "  3. TEST-EMAIL-NOW.bat",
        "  4. RUN-THURSDAY-LIVE.bat or BRIDGE-KEEPALIVE + DUAL-SYNC-LOOP",
        "  5. PREP-MARKET-OPEN.bat",
        "",
        f"Checklist: {CHECKLIST_PATH}",
        "Health: https://spy-options-bridge.onrender.com/health",
    ]
    REMINDER_FILE.parent.mkdir(parents=True, exist_ok=True)
    REMINDER_FILE.write_text("\n".join(body) + "\n", encoding="utf-8")
    print(f"Wrote {REMINDER_FILE}")


def send_reminder_email(lines: list[str]) -> bool:
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    body = (
        f"Pre-market checklist — items still open before 9:30 AM ET.\n\n"
        f"Time: {ts}\n\n"
        + "\n".join(lines)
        + "\n\n"
        f"Checklist file: {CHECKLIST_PATH}\n"
        "First step: SETUP-EMAIL-AUTOMATION.bat on Desktop.\n"
        "Render: add EMAIL_* then Manual Deploy.\n"
    )
    summary = "\n".join(lines[:3]) if lines else "All checklist items done."
    return notify_premarket_reminder(summary)


def run_reminder(*, force: bool = False) -> int:
    _load_dotenv()
    missing = incomplete_items()
    if not missing:
        print("Nothing to remind — checklist complete.")
        return 0
    if not force and not before_market_open():
        print("After 9:30 ET or weekend — skipping email (use --force to override).")
        print_status(quick=True)
        return 0

    lines = format_incomplete_lines()
    tc.write_human_summary()
    write_reminder_file(lines)
    sent = send_reminder_email(lines)
    if sent:
        print(f"Reminder email sent to configured EMAIL_TO (default {DEFAULT_TO}).")
    else:
        print("Email not sent (SMTP not configured — complete SETUP-EMAIL-AUTOMATION.bat).")
    return 1


def sleep_until_9am_et() -> None:
    while True:
        now = datetime.now(ET)
        if not is_weekday_market_day(now):
            print("Weekend — schedule mode exiting (run again Monday).")
            return
        target = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if now >= target:
            break
        wait_sec = (target - now).total_seconds()
        print(f"Schedule: sleeping until 9:00 AM ET ({int(wait_sec)}s)...")
        time.sleep(min(wait_sec, 3600))
    print("9:00 AM ET — running reminder.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-market incomplete-task reminder")
    parser.add_argument("--quick", action="store_true", help="Print incomplete items only")
    parser.add_argument("--schedule", action="store_true", help="Sleep until 9:00 ET then remind")
    parser.add_argument("--force", action="store_true", help="Send reminder even after 9:30 ET")
    args = parser.parse_args()

    if args.quick:
        tc.write_human_summary()
        return print_status(quick=True)

    if args.schedule:
        sleep_until_9am_et()
        return run_reminder(force=True)

    return run_reminder(force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
