#!/usr/bin/env python3
"""End-of-day comprehensive session report — goals, bridge, Alpaca, journal, Command Center."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from live_session_guardian import (  # noqa: E402
    alpaca_line,
    bridge_health,
    journal_path,
    load_env,
    worker_line,
)
from paper_pnl_audit import account_snapshot, recent_mleg_orders  # noqa: E402

ET = ZoneInfo("America/New_York")
DESKTOP = Path(r"C:\Users\Shiel\Desktop")
RENDER = "https://spy-options-bridge.onrender.com/health"
CC_LOG = DESKTOP / "COMMAND-CENTER-LOG.txt"
DUAL_LOG = DESKTOP / "DUAL-SYNC-LOG.txt"
BURST_LOG = DESKTOP / "BURST-PAPER-LOG.txt"


def tail(path: Path, n: int = 40) -> str:
    if not path.is_file():
        return f"(missing {path.name})"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-n:]) if lines else "(empty)"


def goals_scorecard(env: dict[str, str], bridge: dict[str, str]) -> list[str]:
    rows = [
        "GOALS SCORECARD (today's live run)",
        "  [ ] MACD TV alert delivered -> Render (check TV alert log)",
        "  [ ] At least one PUT_CREDIT_SPREAD fill in Alpaca Activities",
        "  [ ] Bridge stayed reachable (version + green/yellow risk)",
        "  [ ] Command Center usable (GUI or guardian journal)",
        "  [ ] End-of-day equity / P&L understood",
    ]
    if bridge.get("ok") == "true":
        rows[2] = rows[2].replace("[ ]", "[~]", 1) + f" — bridge {bridge.get('version')} tv_risk={bridge.get('tv_risk')}"
    acc = account_snapshot(env)
    if "error" not in acc:
        rows[4] = rows[4].replace("[ ]", "[~]", 1) + f" — equity={acc.get('equity')}"
    orders = recent_mleg_orders(env, limit=12)
    filled = [o for o in orders if o.get("status") == "filled" and "error" not in o]
    if filled:
        rows[1] = rows[1].replace("[ ]", "[x]", 1) + f" — {len(filled)} filled mleg row(s) in recent orders"
    return rows


def build_report(*, email_note: str = "") -> str:
    now = datetime.now(ET)
    day = now.strftime("%Y-%m-%d")
    env = load_env()
    bridge = bridge_health()
    acc = account_snapshot(env)
    orders = recent_mleg_orders(env, limit=15)

    parts = [
        f"SPY LIVE SESSION — END OF DAY REPORT",
        f"Date: {day} | Generated: {now.strftime('%H:%M:%S ET')}",
        "=" * 60,
        "",
        *goals_scorecard(env, bridge),
        "",
        "BRIDGE (Render)",
        f"  {bridge}",
        "",
        "ALPACA ACCOUNT",
        f"  {acc}",
        "",
        "WORKERS (PC)",
        f"  {worker_line()}",
        "",
        "RECENT MLEG ORDERS (newest first)",
    ]
    if not orders:
        parts.append("  (none)")
    else:
        for o in orders[:10]:
            parts.append(
                f"  {o.get('status','?')} {o.get('symbol','?')} qty={o.get('qty','?')} "
                f"filled={o.get('filled_qty','?')} @ {o.get('filled_at','?')}"
            )

    jpath = journal_path(now)
    parts.extend(
        [
            "",
            f"GUARDIAN JOURNAL ({jpath.name})",
            tail(jpath, 80),
            "",
            "COMMAND CENTER LOG (tail)",
            tail(CC_LOG, 25),
            "",
            "DUAL-SYNC LOG (tail)",
            tail(DUAL_LOG, 15),
            "",
            "BURST LOG (tail)",
            tail(BURST_LOG, 15),
            "",
            "SALVAGE NOTES",
            "  • Production path: TradingView MACD -> Render /entry -> Alpaca paper (weekly spread).",
            "  • Today: Command Center GUI unstable — use guardian + EOD report; CC 2.0 planned.",
            "  • Do not run BURST-100 during live MACD; bridge 5.5.11 has memory caps.",
            "",
            "NEXT (Cursor / Grok / you)",
            "  1. Read this report + Activities in Alpaca.",
            "  2. Reply go CC2 when ready for Command Center 2.0 UI/architecture pass.",
            "  3. Off-hours: PAPER-PNL-AUDIT.bat --try-entry if fills still unproven.",
        ]
    )
    if email_note:
        parts.extend(["", "EMAIL", email_note])
    return "\n".join(parts) + "\n"


def register_eod_task() -> None:
    """Windows scheduled task: EOD report ~16:05 ET weekdays."""
    if sys.platform != "win32":
        return
    bat = ROOT / "launchers" / "EOD-SESSION-REPORT.bat"
    if not bat.is_file():
        return
    name = "SPY-EOD-SESSION-REPORT"
    ps = (
        f"$name = '{name}'; "
        f"$bat = '{bat}'; "
        "Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue; "
        "$action = New-ScheduledTaskAction -Execute 'cmd.exe' "
        "-Argument ('/c \"' + $bat + '\"'); "
        "$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 4:05PM; "
        "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries; "
        "Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings "
        "-Description 'SPY EOD session report + email' | Out-Null"
    )
    import subprocess

    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        timeout=60,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EOD session report")
    parser.add_argument("--email", action="store_true", help="Send report via SMTP if configured")
    parser.add_argument("--register-task", action="store_true", help="Register 16:05 ET weekday EOD task")
    parser.add_argument("--out", type=str, default="")
    args = parser.parse_args(argv)

    if args.register_task:
        register_eod_task()
        print("Registered scheduled task SPY-EOD-SESSION-REPORT (16:05 ET weekdays)")

    report = build_report()
    day = datetime.now(ET).strftime("%Y-%m-%d")
    out = Path(args.out) if args.out else DESKTOP / f"EOD-SESSION-REPORT-{day}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    try:
        print(report)
    except UnicodeEncodeError:
        print(report.encode("ascii", errors="replace").decode("ascii"))
    print(f"WROTE {out}")

    if args.email:
        try:
            from team_email import send_email_alert  # noqa: E402

            subject = f"[SPY Command Center] report: EOD Session {day}"
            ok = send_email_alert(subject, report[:24000])
            print(f"EMAIL {'sent' if ok else 'skipped/failed'}")
        except Exception as exc:
            print(f"EMAIL error {type(exc).__name__}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
