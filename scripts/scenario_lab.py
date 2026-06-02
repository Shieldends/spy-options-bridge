#!/usr/bin/env python3
"""
Scenario Lab — multi-scenario unattended validation (paper only).
Reads config/scenario_lab.yaml. Writes Desktop/SCENARIO-LAB-RESULT.txt
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from exercise_system import (  # noqa: E402
    BRIDGE,
    RunLog,
    cancel_open_mleg,
    close_spread_warning,
    count_spy_option_positions,
    latest_spy,
    load_env,
    market_clock,
    poll_order,
    run_pytest,
    strikes_from_order,
    wait_exits,
    warning_post,
)

CONFIG_PATH = ROOT / "config" / "scenario_lab.yaml"
DESKTOP_REPORT = Path.home() / "Desktop" / "SCENARIO-LAB-RESULT.txt"
DESKTOP_REMINDER = Path.home() / "Desktop" / "PAUSE-TV-ENTRY-REMINDER.txt"
ET = ZoneInfo("America/New_York")
ANALYZER = Path.home() / "market-analyzer"


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def undo(env: dict[str, str], secret: str, log: RunLog) -> None:
    cancel_open_mleg(env, log)
    close_spread_warning(env, secret, log, None)
    cancel_open_mleg(env, log)
    time.sleep(2)


def entry_post(secret: str, spot: float, fill_mode: str, cfg: dict) -> tuple[bool, str | None, dict]:
    body = {
        "webhookSecret": secret,
        "ticker": "SPY",
        "action": "PUT_CREDIT_SPREAD",
        "signalPrice": spot,
        "dteFilter": "weekly",
        "strikeOffsetShort": -5,
        "strikeOffsetLong": -6,
        "quantity": 1,
        "fillMode": fill_mode,
        "limitCredit": 0.55,
    }
    r = httpx.post(f"{cfg.get('bridge_url', BRIDGE)}/entry", json=body, timeout=90)
    data = r.json() if r.is_success else {}
    oid = ((data.get("entry") or {}).get("broker_response") or {}).get("id")
    return bool(data.get("success")), str(oid) if oid else None, data


def run_autopilot(log: RunLog) -> None:
    py = ANALYZER / ".venv" / "Scripts" / "python.exe"
    script = ANALYZER / "scripts" / "autopilot.py"
    if not script.exists():
        log.warn("autopilot skipped (market-analyzer not found)")
        return
    log.info("AUTOPILOT: starting (backtest + prelaunch)…")
    t0 = time.time()
    proc = subprocess.run([str(py), str(script)], cwd=ANALYZER, capture_output=True, text=True)
    mins = (time.time() - t0) / 60
    if proc.returncode == 0:
        log.ok(f"autopilot done ({mins:.1f} min)")
    else:
        log.warn(f"autopilot exit {proc.returncode} ({mins:.1f} min)")
    log.info(f"Reports: {ANALYZER / 'reports'}")


def scenario_S1(env: dict, secret: str, cfg: dict, log: RunLog, market_open: bool) -> None:
    run_pytest(log)
    r = httpx.get(f"{cfg.get('bridge_url', BRIDGE)}/health", timeout=30)
    if r.is_success and r.json().get("status") == "ok":
        log.ok(f"S1 health {r.json().get('version')}")
    else:
        log.fail(f"S1 health HTTP {r.status_code}")


def scenario_S2(env: dict, secret: str, cfg: dict, log: RunLog, market_open: bool) -> None:
    body = {"webhookSecret": "bad", "ticker": "SPY", "action": "PUT_CREDIT_SPREAD", "signalPrice": 1}
    r = httpx.post(f"{cfg.get('bridge_url', BRIDGE)}/webhook", json=body, timeout=30)
    if r.status_code == 401:
        log.ok("S2 webhook rejects bad secret")
    else:
        log.fail(f"S2 expected 401 got {r.status_code}")


def scenario_entry_fill(
    env: dict,
    secret: str,
    cfg: dict,
    log: RunLog,
    sid: str,
    fill_mode: str,
) -> tuple[float, float] | None:
    undo(env, secret, log)
    spot = latest_spy(env)
    ok, oid, _ = entry_post(secret, spot, fill_mode, cfg)
    if not ok or not oid:
        log.fail(f"{sid} entry rejected")
        return None
    status = poll_order(env, oid, log)
    if status != "filled":
        log.fail(f"{sid} not filled ({status})")
        undo(env, secret, log)
        return None
    log.ok(f"{sid} entry FILLED ({fill_mode})")
    return strikes_from_order(env, oid)


def scenario_S3(env, secret, cfg, log, market_open) -> None:
    if not market_open:
        log.info("S3 SKIP (market closed)")
        return
    scenario_entry_fill(env, secret, cfg, log, "S3", cfg["scenarios"]["S3_entry_exercise_fill"].get("fill_mode", "exercise"))
    undo(env, secret, log)


def scenario_S4(env, secret, cfg, log, market_open) -> None:
    if not market_open:
        log.info("S4 SKIP (market closed)")
        return
    strikes = scenario_entry_fill(env, secret, cfg, log, "S4", "exercise")
    if strikes:
        n = wait_exits(env, log)
        if n > 0:
            log.ok(f"S4 GTC exits resting ({n})")
        else:
            log.warn("S4 no GTC exits seen")
    undo(env, secret, log)


def scenario_S5(env, secret, cfg, log, market_open) -> None:
    if not market_open:
        log.info("S5 SKIP (market closed)")
        return
    spot = latest_spy(env)
    short_est = round(spot) - 5
    data = warning_post(
        secret,
        {
            "ticker": "SPY",
            "signalPrice": short_est * 0.999,
            "strikeOffsetShort": -5,
            "strikeOffsetLong": -6,
            "overrideAutoClose": True,
        },
        log,
        "S5 warning notify",
    )
    if data.get("action_taken") == "notify_only_override":
        log.ok("S5 warning notify-only")
    else:
        log.warn(f"S5 action={data.get('action_taken')}")


def scenario_S6(env, secret, cfg, log, market_open) -> None:
    if not market_open:
        log.info("S6 SKIP (market closed)")
        return
    strikes = scenario_entry_fill(env, secret, cfg, log, "S6", "exercise")
    if not strikes:
        return
    cancel_open_mleg(env, log)
    time.sleep(2)
    if close_spread_warning(env, secret, log, strikes):
        log.ok("S6 warning auto-close")
    time.sleep(5)
    undo(env, secret, log)


def scenario_S7(env, secret, cfg, log, market_open) -> None:
    if not market_open:
        log.info("S7 SKIP (market closed)")
        return
    scenario_entry_fill(
        env,
        secret,
        cfg,
        log,
        "S7",
        cfg["scenarios"]["S7_entry_aggressive"].get("fill_mode", "aggressive"),
    )
    undo(env, secret, log)


def scenario_S8(env, secret, cfg, log, market_open: bool) -> None:
    undo(env, secret, log)
    pos = count_spy_option_positions(env)
    if pos == 0:
        log.ok("S8 account flat")
    else:
        log.fail(f"S8 still {pos} SPY option leg(s)")


SCENARIO_RUNNERS = [
    ("S1_pytest_health", scenario_S1),
    ("S2_auth_reject", scenario_S2),
    ("S3_entry_exercise_fill", scenario_S3),
    ("S4_gtc_exits", scenario_S4),
    ("S5_warning_notify", scenario_S5),
    ("S6_warning_close", scenario_S6),
    ("S7_entry_aggressive", scenario_S7),
    ("S8_account_flat", scenario_S8),
]


def write_tv_reminder() -> None:
    DESKTOP_REMINDER.write_text(
        "SCENARIO LAB RUNNING OR SCHEDULED\n"
        "================================\n"
        "Pause your TradingView ENTRY alert for ~30 minutes\n"
        "(Warning can stay on or pause too.)\n"
        "Re-enable ENTRY after SCENARIO-LAB-RESULT.txt shows DONE.\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-autopilot", action="store_true")
    args = parser.parse_args()
    cfg = load_config()
    log = RunLog()
    log.info("=== SCENARIO LAB ===")
    env = load_env()
    secret = env.get("WEBHOOK_SECRET", "")
    if not secret:
        log.fail("WEBHOOK_SECRET missing")
        return 1

    if cfg.get("write_tv_pause_reminder", True):
        write_tv_reminder()

    if cfg.get("run_autopilot_first") and not args.no_autopilot:
        run_autopilot(log)

    clock = market_clock(env)
    market_open = bool(clock.get("is_open"))
    log.info(f"Market open={market_open}")

    enabled = cfg.get("scenarios", {})
    live_count = 0
    max_live = int(cfg.get("max_live_scenarios", 6))
    delay = float(cfg.get("delay_between_scenarios_sec", 5))

    for key, fn in SCENARIO_RUNNERS:
        sc = enabled.get(key, {})
        if not sc.get("enabled", True):
            log.info(f"{key} disabled in yaml")
            continue
        if sc.get("needs_market") and not market_open:
            log.info(f"{key} SKIP (market closed)")
            continue
        if sc.get("needs_market"):
            if live_count >= max_live:
                log.info(f"{key} SKIP (max live scenarios {max_live})")
                continue
            live_count += 1
        log.info(f"--- {key} ---")
        fn(env, secret, cfg, log, market_open)
        time.sleep(delay)

    log.info(f"SCORE PASS={log.pass_n} FAIL={log.fail_n} WARN={log.warn_n}")
    log.info("DONE — re-enable TradingView ENTRY alert for normal trading")
    summary = f"\n{'='*40}\nPASS={log.pass_n} FAIL={log.fail_n} WARN={log.warn_n}\nDONE\n"
    log.lines.append(summary.strip())
    DESKTOP_REPORT.write_text("\n".join(log.lines) + "\n", encoding="utf-8")
    print(summary)
    print(f"Report: {DESKTOP_REPORT}")
    return 0 if log.fail_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())