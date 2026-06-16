#!/usr/bin/env python3
"""
Paper pipe stress — paced opens + timed closes (no TradingView).

  python scripts/stress_pipe.py              # default: 3 cycles x 20 opens
  python scripts/stress_pipe.py --quick      # 2 cycles x 5 opens
  python scripts/stress_pipe.py --cycles 5 --opens 30

Pause BOTH SPY TV alerts (Entry + Warning) before running.
Uses /exercise/entry (opens) and /warning forceAutoClose (closes).
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
RENDER = "https://spy-options-bridge.onrender.com"
ET = ZoneInfo("America/New_York")
LOG_PATH = Path(r"C:\Users\Shiel\Desktop\STRESS-PIPE-LOG.txt")


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV.exists():
        return out
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def alpaca_headers(env: dict[str, str]) -> dict[str, str]:
    return {
        "Apca-Api-Key-Id": env.get("APCA_API_KEY_ID") or env.get("ALPACA_API_KEY", ""),
        "Apca-Api-Secret-Key": env.get("APCA_API_SECRET_KEY") or env.get("ALPACA_SECRET_KEY", ""),
    }


def alpaca_base(env: dict[str, str]) -> str:
    return env.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")


def latest_spy(env: dict[str, str]) -> float:
    h = alpaca_headers(env)
    if not h["Apca-Api-Key-Id"]:
        return 590.0
    try:
        r = httpx.get(
            "https://data.alpaca.markets/v2/stocks/SPY/trades/latest",
            headers=h,
            timeout=20,
        )
        if r.is_success:
            p = r.json().get("trade", {}).get("p")
            if p:
                return float(p)
    except Exception:
        pass
    return 590.0


def market_clock(env: dict[str, str]) -> dict:
    h = alpaca_headers(env)
    if not h["Apca-Api-Key-Id"]:
        return {}
    r = httpx.get(f"{alpaca_base(env)}/v2/clock", headers=h, timeout=20)
    return r.json() if r.is_success else {}


def account_equity(env: dict[str, str]) -> float | None:
    h = alpaca_headers(env)
    if not h["Apca-Api-Key-Id"]:
        return None
    r = httpx.get(f"{alpaca_base(env)}/v2/account", headers=h, timeout=20)
    if not r.is_success:
        return None
    try:
        return float(r.json().get("equity") or 0)
    except (TypeError, ValueError):
        return None


def count_spy_option_legs(env: dict[str, str]) -> int:
    h = alpaca_headers(env)
    if not h["Apca-Api-Key-Id"]:
        return -1
    r = httpx.get(f"{alpaca_base(env)}/v2/positions", headers=h, timeout=30)
    if not r.is_success:
        return -1
    n = 0
    for p in r.json():
        sym = (p.get("symbol") or "").upper()
        if sym.startswith("SPY") and float(p.get("qty") or 0) != 0:
            n += 1
    return n


def log_line(msg: str, log_file: Path) -> None:
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    line = f"{ts} | {msg}"
    print(line)
    try:
        with log_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def run_open(base_url: str, secret: str, spy: float, timeout: float) -> dict:
    body = {
        "webhookSecret": secret,
        "ticker": "SPY",
        "action": "PUT_CREDIT_SPREAD",
        "signalPrice": spy,
        "dteFilter": "0dte",
        "strikeOffsetShort": -10,
        "strikeOffsetLong": -15,
        "quantity": 1,
        "fillMode": "exercise",
        "skipExits": True,
    }
    r = httpx.post(f"{base_url}/exercise/entry", json=body, timeout=timeout)
    try:
        return r.json()
    except Exception:
        return {"success": False, "message": f"HTTP {r.status_code}", "filled": False}


def run_close(base_url: str, secret: str, spy: float, timeout: float) -> dict:
    body = {
        "webhookSecret": secret,
        "ticker": "SPY",
        "signalPrice": spy,
        "strikeOffsetShort": -10,
        "strikeOffsetLong": -15,
        "forceAutoClose": True,
    }
    r = httpx.post(f"{base_url}/warning", json=body, timeout=timeout)
    try:
        return r.json()
    except Exception:
        return {"success": False, "action_taken": f"HTTP {r.status_code}"}


def open_phase(
    base_url: str,
    secret: str,
    env: dict[str, str],
    *,
    count: int,
    interval: float,
    timeout: float,
    log_file: Path,
    cycle: int,
) -> tuple[int, int]:
    filled = 0
    for i in range(1, count + 1):
        spy = latest_spy(env)
        data = run_open(base_url, secret, spy, timeout)
        ok = bool(data.get("filled") or data.get("success"))
        if ok:
            filled += 1
        oid = str(data.get("order_id") or "—")[:12]
        status = data.get("status") or data.get("message", "?")
        log_line(
            f"C{cycle} OPEN #{i}/{count} filled={'YES' if ok else 'NO'} "
            f"order={oid} status={str(status)[:60]}",
            log_file,
        )
        if i < count and interval > 0:
            time.sleep(interval)
    return filled, count


def close_phase(
    base_url: str,
    secret: str,
    env: dict[str, str],
    *,
    max_attempts: int,
    interval: float,
    timeout: float,
    log_file: Path,
    cycle: int,
) -> int:
    closed = 0
    for attempt in range(1, max_attempts + 1):
        legs = count_spy_option_legs(env)
        if legs == 0:
            log_line(f"C{cycle} CLOSE flat (0 SPY option legs)", log_file)
            break
        if legs < 0:
            log_line(f"C{cycle} CLOSE WARN could not read positions", log_file)
            break
        spy = latest_spy(env)
        data = run_close(base_url, secret, spy, timeout)
        action = data.get("action_taken") or data.get("message") or "?"
        ok = action in {
            "auto_close_submitted",
            "closed",
            "accepted",
            "auto_close_submitted_async",
        }
        if ok:
            closed += 1
        log_line(
            f"C{cycle} CLOSE try #{attempt} legs_before={legs} action={action}",
            log_file,
        )
        time.sleep(max(interval, 12.0))
        if count_spy_option_legs(env) == 0:
            log_line(f"C{cycle} CLOSE flat after try #{attempt}", log_file)
            break
    return closed


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper pipe stress — opens + timed closes")
    parser.add_argument("--cycles", type=int, default=3, help="Open/close cycles (default 3)")
    parser.add_argument("--opens", type=int, default=20, help="Opens per cycle (default 20)")
    parser.add_argument("--open-interval", type=float, default=3.0, help="Seconds between opens")
    parser.add_argument("--close-max", type=int, default=12, help="Max close attempts per cycle")
    parser.add_argument("--close-interval", type=float, default=5.0, help="Seconds between close tries")
    parser.add_argument("--cycle-pause", type=float, default=45.0, help="Pause between cycles (sec)")
    parser.add_argument("--pre-close-wait", type=float, default=10.0, help="Wait after opens before closes")
    parser.add_argument("--quick", action="store_true", help="2 cycles x 5 opens")
    parser.add_argument("--timeout", type=float, default=180.0, help="HTTP timeout per call")
    parser.add_argument("--log", type=str, default=str(LOG_PATH), help="Log file path")
    args = parser.parse_args()

    if args.quick:
        args.cycles = 2
        args.opens = 5
        args.cycle_pause = 20.0

    env = load_env()
    secret = env.get("WEBHOOK_SECRET", "")
    if not secret:
        print("FAIL: WEBHOOK_SECRET missing in .env")
        return 1

    log_file = Path(args.log)
    base_url = RENDER

    hr = httpx.get(f"{base_url}/health", timeout=60)
    if not hr.is_success:
        print(f"FAIL health HTTP {hr.status_code}")
        return 1
    health = hr.json()
    ver = health.get("version", "?")
    loss_lim = health.get("spread_daily_loss_limit", "?")
    min_cr = health.get("spread_min_credit", "?")
    print(f"Bridge v{ver} loss_limit={loss_lim} min_credit={min_cr}")

    clock = market_clock(env)
    if clock and not clock.get("is_open"):
        print("WARN: market appears closed — fills may not happen")

    eq_start = account_equity(env)
    log_line(
        f"START cycles={args.cycles} opens/cycle={args.opens} "
        f"open_iv={args.open_interval}s equity_start={eq_start}",
        log_file,
    )

    total_filled = 0
    total_open_attempts = 0
    total_close_tries = 0

    for cycle in range(1, args.cycles + 1):
        log_line(f"=== CYCLE {cycle}/{args.cycles} OPEN PHASE ===", log_file)
        filled, n = open_phase(
            base_url,
            secret,
            env,
            count=args.opens,
            interval=args.open_interval,
            timeout=args.timeout,
            log_file=log_file,
            cycle=cycle,
        )
        total_filled += filled
        total_open_attempts += n

        if args.pre_close_wait > 0:
            log_line(f"C{cycle} pause {args.pre_close_wait}s before closes", log_file)
            time.sleep(args.pre_close_wait)

        log_line(f"=== CYCLE {cycle}/{args.cycles} CLOSE PHASE ===", log_file)
        legs = count_spy_option_legs(env)
        log_line(f"C{cycle} legs open before close={legs}", log_file)
        c = close_phase(
            base_url,
            secret,
            env,
            max_attempts=args.close_max,
            interval=args.close_interval,
            timeout=args.timeout,
            log_file=log_file,
            cycle=cycle,
        )
        total_close_tries += c

        eq = account_equity(env)
        log_line(f"C{cycle} END equity={eq} legs={count_spy_option_legs(env)}", log_file)

        if cycle < args.cycles and args.cycle_pause > 0:
            log_line(f"C{cycle} cycle pause {args.cycle_pause}s", log_file)
            time.sleep(args.cycle_pause)

    eq_end = account_equity(env)
    log_line(
        f"DONE filled={total_filled}/{total_open_attempts} "
        f"close_rounds={total_close_tries} equity_start={eq_start} equity_end={eq_end}",
        log_file,
    )
    print(f"\nRESULT: {total_filled}/{total_open_attempts} opens filled")
    print(f"Equity: {eq_start} -> {eq_end}")
    print(f"Log: {log_file}")
    print("Run SPREAD-ACTIVITY.bat for digest.")
    return 0 if total_filled > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
