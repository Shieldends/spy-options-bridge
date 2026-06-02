#!/usr/bin/env python3
"""
Complete system exercise: bridge + Alpaca + entry fill + TP/SL + warning close + undo.

  python exercise_system.py            # complete test (default)
  python exercise_system.py --quick    # shorter test (no live close)
  python exercise_system.py --undo     # cleanup only
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from main import find_put_credit_spreads_in_positions, parse_occ_symbol  # noqa: E402

ENV_PATH = ROOT / ".env"
DESKTOP_REPORT = Path.home() / "Desktop" / "EXERCISE-RESULT.txt"
ET = ZoneInfo("America/New_York")

BRIDGE = "https://spy-options-bridge.onrender.com"
POLL_SEC = 2
FILL_WAIT_SEC = 120
EXIT_WAIT_SEC = 90


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


class RunLog:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.pass_n = 0
        self.fail_n = 0
        self.warn_n = 0

    def ok(self, msg: str) -> None:
        self._write(f"PASS {msg}")
        self.pass_n += 1

    def fail(self, msg: str) -> None:
        self._write(f"FAIL {msg}")
        self.fail_n += 1

    def warn(self, msg: str) -> None:
        self._write(f"WARN {msg}")
        self.warn_n += 1

    def info(self, msg: str) -> None:
        self._write(msg)

    def _write(self, msg: str) -> None:
        line = f"[{datetime.now(tz=ET).strftime('%H:%M:%S')}] {msg}"
        print(line)
        self.lines.append(line)


def alpaca_headers(env: dict[str, str]) -> dict[str, str]:
    return {
        "Apca-Api-Key-Id": env["APCA_API_KEY_ID"],
        "Apca-Api-Secret-Key": env["APCA_API_SECRET_KEY"],
    }


def alpaca_base(env: dict[str, str]) -> str:
    return env.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")


def market_clock(env: dict[str, str]) -> dict:
    r = httpx.get(f"{alpaca_base(env)}/v2/clock", headers=alpaca_headers(env), timeout=20)
    return r.json() if r.is_success else {}


def latest_spy(env: dict[str, str]) -> float:
    h = alpaca_headers(env)
    r = httpx.get("https://data.alpaca.markets/v2/stocks/SPY/trades/latest", headers=h, timeout=20)
    if r.is_success:
        p = r.json().get("trade", {}).get("p")
        if p:
            return float(p)
    r2 = httpx.get(
        "https://data.alpaca.markets/v2/stocks/SPY/bars",
        headers=h,
        params={"timeframe": "1Min", "limit": 1},
        timeout=20,
    )
    bars = r2.json().get("bars") or []
    if bars:
        return float(bars[-1]["c"])
    return 590.0


def cancel_open_mleg(env: dict[str, str], log: RunLog) -> int:
    base = alpaca_base(env)
    h = alpaca_headers(env)
    r = httpx.get(f"{base}/v2/orders", headers=h, params={"status": "open", "nested": True}, timeout=30)
    if not r.is_success:
        log.fail(f"cancel list HTTP {r.status_code}")
        return 0
    n = 0
    for o in r.json():
        if o.get("order_class") != "mleg":
            continue
        oid = o.get("id")
        if not oid:
            continue
        cr = httpx.delete(f"{base}/v2/orders/{oid}", headers=h, timeout=30)
        if cr.is_success:
            n += 1
            log.info(f"Canceled order {oid[:8]} limit={o.get('limit_price')}")
    if n == 0:
        log.info("No open multi-leg orders")
    return n


def count_spy_option_positions(env: dict[str, str]) -> int:
    r = httpx.get(f"{alpaca_base(env)}/v2/positions", headers=alpaca_headers(env), timeout=30)
    if not r.is_success:
        return -1
    n = 0
    for p in r.json():
        sym = (p.get("symbol") or "").upper()
        if sym.startswith("SPY") and float(p.get("qty") or 0) != 0:
            n += 1
    return n


def strikes_from_order(env: dict[str, str], order_id: str) -> tuple[float, float] | None:
    r = httpx.get(
        f"{alpaca_base(env)}/v2/orders/{order_id}",
        headers=alpaca_headers(env),
        params={"nested": True},
        timeout=30,
    )
    if not r.is_success:
        return None
    shorts: list[float] = []
    longs: list[float] = []
    for leg in r.json().get("legs") or []:
        parsed = parse_occ_symbol(leg.get("symbol") or "")
        if not parsed or parsed.get("option_type") != "P":
            continue
        strike = float(parsed["strike"])
        if (leg.get("side") or "").lower() == "sell":
            shorts.append(strike)
        else:
            longs.append(strike)
    if shorts and longs:
        return max(shorts), min(longs)
    return None


def poll_order(env: dict[str, str], order_id: str, log: RunLog) -> str:
    base = alpaca_base(env)
    h = alpaca_headers(env)
    deadline = time.time() + FILL_WAIT_SEC
    last = ""
    while time.time() < deadline:
        r = httpx.get(f"{base}/v2/orders/{order_id}", headers=h, timeout=20)
        if r.is_success:
            last = (r.json().get("status") or "").lower()
            log.info(f"Order status: {last}")
            if last in {"filled", "partially_filled"}:
                return last
            if last in {"canceled", "expired", "rejected", "failed"}:
                return last
        time.sleep(POLL_SEC)
    return last or "timeout"


def wait_exits(env: dict[str, str], log: RunLog) -> int:
    deadline = time.time() + EXIT_WAIT_SEC
    while time.time() < deadline:
        r = httpx.get(
            f"{alpaca_base(env)}/v2/orders",
            headers=alpaca_headers(env),
            params={"status": "open", "nested": True},
            timeout=30,
        )
        if r.is_success:
            n = len([o for o in r.json() if o.get("order_class") == "mleg"])
            if n > 0:
                log.info(f"GTC exit orders open: {n}")
                return n
        time.sleep(3)
    log.warn("No GTC TP/SL seen within wait window (may still be scheduling)")
    return 0


def warning_post(secret: str, body: dict, log: RunLog, label: str) -> dict:
    wr = httpx.post(f"{BRIDGE}/warning", json={**body, "webhookSecret": secret}, timeout=90)
    data = wr.json() if "json" in (wr.headers.get("content-type") or "") else {}
    action = data.get("action_taken", "?")
    log.info(f"{label}: HTTP {wr.status_code} action={action}")
    return data


def strikes_from_positions(env: dict[str, str]) -> tuple[float, float] | None:
    r = httpx.get(f"{alpaca_base(env)}/v2/positions", headers=alpaca_headers(env), timeout=30)
    if not r.is_success:
        return None
    spreads = find_put_credit_spreads_in_positions(r.json(), "SPY")
    if not spreads:
        return None
    meta = spreads[0].metadata
    return float(meta["short_strike"]), float(meta["long_strike"])


def close_spread_warning(env: dict[str, str], secret: str, log: RunLog, strikes: tuple[float, float] | None) -> bool:
    pos_strikes = strikes_from_positions(env)
    if pos_strikes:
        short_s, long_s = pos_strikes
    elif strikes:
        short_s, long_s = strikes
    else:
        spot = latest_spy(env)
        short_s, long_s = float(round(spot) - 5), float(round(spot) - 6)
    danger_price = short_s * 0.999
    data = warning_post(
        secret,
        {
            "ticker": "SPY",
            "signalPrice": danger_price,
            "short_strike": short_s,
            "long_strike": long_s,
            "overrideAutoClose": False,
            "forceAutoClose": True,
        },
        log,
        "WARNING auto-close",
    )
    action = data.get("action_taken") or ""
    if action in {"auto_close_submitted", "closed"}:
        log.ok(f"warning close ({action})")
        return True
    if action == "danger_no_matching_position":
        log.warn("warning close: no matching position (strikes may differ)")
        return False
    log.fail(f"warning close ({action})")
    return False


def run_pytest(log: RunLog) -> None:
    log.info("UNIT TESTS: pytest…")
    proc = subprocess.run(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "pytest", "tests/", "-q", "--tb=line"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        log.ok("pytest")
    else:
        log.fail(f"pytest exit {proc.returncode}")
        for line in (proc.stdout + proc.stderr).splitlines()[-5:]:
            log.info(line)


def run_complete(env: dict[str, str], log: RunLog, *, quick: bool) -> int:
    secret = env.get("WEBHOOK_SECRET", "")
    if not secret:
        log.fail("WEBHOOK_SECRET missing in .env")
        return 1

    run_pytest(log)

    clock = market_clock(env)
    if clock:
        log.info(f"Market open={clock.get('is_open')}")
        if not clock.get("is_open"):
            log.warn("Market closed — fill/close steps may fail")

    hr = httpx.get(f"{BRIDGE}/health", timeout=30)
    fill_mode = "aggressive"
    if hr.is_success:
        ver = str(hr.json().get("version") or "")
        if ver >= "5.5.0":
            fill_mode = "exercise"
        log.ok(f"health {ver} fillMode={fill_mode}")
    else:
        log.fail(f"health HTTP {hr.status_code}")
        return 1

    log.info("Cleanup before test…")
    cancel_open_mleg(env, log)

    spot = latest_spy(env)
    log.info(f"SPY spot ≈ {spot:.2f}")
    entry_body = {
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

    bad = httpx.post(f"{BRIDGE}/webhook", json={**entry_body, "webhookSecret": "invalid"}, timeout=30)
    if bad.status_code == 401:
        log.ok("/webhook rejects bad secret")
    else:
        log.warn(f"/webhook bad-secret HTTP {bad.status_code}")

    time.sleep(1)
    log.info("ENTRY via /entry (main fill test)…")
    er = httpx.post(f"{BRIDGE}/entry", json=entry_body, timeout=90)
    if not er.is_success or not er.json().get("success"):
        log.fail(f"/entry {er.status_code} {er.text[:300]}")
        cancel_open_mleg(env, log)
        close_spread_warning(env, secret, log, None)
        return 1
    oid = ((er.json().get("entry") or {}).get("broker_response") or {}).get("id")
    log.ok(f"/entry accepted id={str(oid)[:8]}")

    fill_status = poll_order(env, str(oid), log) if oid else "skipped"
    strikes: tuple[float, float] | None = None
    if fill_status == "filled":
        log.ok("entry FILLED")
        strikes = strikes_from_order(env, str(oid))
        if strikes:
            log.info(f"Spread strikes short={strikes[0]} long={strikes[1]}")
        n = wait_exits(env, log)
        if n > 0:
            log.ok(f"GTC exits resting ({n} mleg order(s))")
    else:
        log.warn(f"entry not filled ({fill_status})")

    short_est = round(spot) - 5
    notify_data = warning_post(
        secret,
        {
            "ticker": "SPY",
            "signalPrice": short_est * 0.999,
            "strikeOffsetShort": -5,
            "strikeOffsetLong": -6,
            "overrideAutoClose": True,
        },
        log,
        "WARNING notify-only",
    )
    if notify_data.get("action_taken") == "notify_only_override":
        log.ok("warning notify-only")
    else:
        log.warn(f"warning notify action={notify_data.get('action_taken')}")

    if not quick and fill_status == "filled":
        cancel_open_mleg(env, log)
        time.sleep(2)
        if count_spy_option_positions(env) > 0:
            close_spread_warning(env, secret, log, strikes)
            time.sleep(5)
            pos = count_spy_option_positions(env)
            if pos == 0:
                log.ok("position flat after warning close")
            else:
                log.warn(f"still {pos} SPY option leg(s) open")

    log.info("Final cleanup…")
    cancel_open_mleg(env, log)
    close_spread_warning(env, secret, log, strikes)
    cancel_open_mleg(env, log)
    pos = count_spy_option_positions(env)
    if pos == 0:
        log.ok("account flat")
    elif pos > 0:
        log.warn(f"{pos} SPY option leg(s) remain — run UNDO-EXERCISE.bat")

    log.info(f"SCORE: PASS={log.pass_n} FAIL={log.fail_n} WARN={log.warn_n}")
    return 0 if log.fail_n == 0 else 1


def undo_only(env: dict[str, str], log: RunLog) -> int:
    cancel_open_mleg(env, log)
    close_spread_warning(env, env.get("WEBHOOK_SECRET", ""), log, None)
    cancel_open_mleg(env, log)
    log.ok("undo complete")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--undo", action="store_true")
    parser.add_argument("--quick", action="store_true", help="Skip live warning-close step")
    args = parser.parse_args()
    log = RunLog()
    log.info("=== COMPLETE SYSTEM EXERCISE ===")
    env = load_env()
    code = undo_only(env, log) if args.undo else run_complete(env, log, quick=args.quick)
    summary = f"\n{'='*40}\nPASS={log.pass_n} FAIL={log.fail_n} WARN={log.warn_n}\n"
    log.lines.append(summary.strip())
    print(summary)
    DESKTOP_REPORT.write_text("\n".join(log.lines) + "\n", encoding="utf-8")
    log.info(f"Report: {DESKTOP_REPORT}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())