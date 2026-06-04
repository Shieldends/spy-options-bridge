#!/usr/bin/env python3
"""
Paper fill burst — prove TV→Bridge→Alpaca execution chain.

  python scripts/burst_paper_fills.py --count 100 --interval 2
  python scripts/burst_paper_fills.py --count 10 --wait-for-open
  python scripts/burst_paper_fills.py --count 5 --local

Logs each attempt: order_id, status, filled yes/no.
Uses /exercise/entry (sync chase, min $0.01 credit, cancel stale).
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
LOG_PATH = Path(r"C:\Users\Shiel\Desktop\BURST-PAPER-LOG.txt")


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
    base = alpaca_base(env)
    try:
        q = httpx.get(f"{base}/v2/stocks/SPY/quotes/latest", headers=h, timeout=20)
        if q.is_success:
            ap = q.json().get("quote", {}).get("ap")
            if ap:
                return float(ap)
    except Exception:
        pass
    return 590.0


def market_clock(env: dict[str, str]) -> dict:
    h = alpaca_headers(env)
    if not h["Apca-Api-Key-Id"]:
        return {}
    r = httpx.get(f"{alpaca_base(env)}/v2/clock", headers=h, timeout=20)
    return r.json() if r.is_success else {}


def wait_until_market_open(env: dict[str, str], *, after_open_sec: int = 60) -> None:
    """Sleep until ~9:31 ET (60s after regular open)."""
    print("Waiting for market open (target 9:31 ET)…")
    while True:
        clock = market_clock(env)
        if clock.get("is_open"):
            if after_open_sec <= 0:
                print("Market is open — starting burst")
                return
            print(f"Market open — pausing {after_open_sec}s then burst")
            time.sleep(after_open_sec)
            return
        next_open = clock.get("next_open")
        now_et = datetime.now(ET)
        print(f"  {now_et.strftime('%H:%M:%S ET')} closed — next_open={next_open}")
        time.sleep(30)


def log_line(msg: str, log_file: Path) -> None:
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    line = f"{ts} | {msg}"
    print(line)
    try:
        with log_file.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def run_single(base_url: str, secret: str, spy: float, timeout: float) -> dict:
    body = {
        "webhookSecret": secret,
        "ticker": "SPY",
        "action": "PUT_CREDIT_SPREAD",
        "signalPrice": spy,
        "dteFilter": "weekly",
        "strikeOffsetShort": -5,
        "strikeOffsetLong": -6,
        "quantity": 1,
        "fillMode": "exercise",
    }
    r = httpx.post(f"{base_url}/exercise/entry", json=body, timeout=timeout)
    try:
        return r.json()
    except Exception:
        return {"success": False, "message": f"HTTP {r.status_code}", "filled": False}


def run_batch_burst(
    base_url: str,
    secret: str,
    spy: float,
    batch_size: int,
    interval: float,
    timeout: float,
) -> dict:
    body = {
        "webhookSecret": secret,
        "ticker": "SPY",
        "action": "PUT_CREDIT_SPREAD",
        "signalPrice": spy,
        "dteFilter": "weekly",
        "strikeOffsetShort": -5,
        "strikeOffsetLong": -6,
        "quantity": 1,
        "fillMode": "exercise",
        "burstCount": batch_size,
        "burstInterval": interval,
        "skipExits": True,
    }
    r = httpx.post(
        f"{base_url}/exercise/burst",
        params={"count": batch_size, "interval": interval},
        json=body,
        timeout=timeout,
    )
    try:
        return r.json()
    except Exception:
        return {"success": False, "message": f"HTTP {r.status_code}", "filled_count": 0, "attempts": []}


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper burst fills via Render bridge")
    parser.add_argument("--count", type=int, default=100, help="Total fill attempts")
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between attempts")
    parser.add_argument("--batch-size", type=int, default=1, help="Use /exercise/burst batch (1=single entry)")
    parser.add_argument("--wait-for-open", action="store_true", help="Wait until 9:31 ET before burst")
    parser.add_argument("--after-open-sec", type=int, default=60, help="Extra wait after open (default 60)")
    parser.add_argument("--local", action="store_true", help="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=180.0, help="HTTP timeout per request")
    parser.add_argument("--log", type=str, default=str(LOG_PATH), help="Desktop log file path")
    args = parser.parse_args()

    env = load_env()
    secret = env.get("WEBHOOK_SECRET", "")
    if not secret:
        print("FAIL: WEBHOOK_SECRET missing in .env")
        return 1

    base_url = "http://127.0.0.1:8000" if args.local else RENDER
    log_file = Path(args.log)
    if not args.local and args.batch_size > 10:
        print(
            "WARN: Render caps /exercise/burst at 10 per request (OOM guard). "
            "Use --batch-size 5 or keep default 1; total --count can stay high."
        )
        args.batch_size = min(args.batch_size, 10)

    hr = httpx.get(f"{base_url}/health", timeout=30)
    if not hr.is_success:
        print(f"FAIL health HTTP {hr.status_code}")
        return 1
    health = hr.json()
    ver = health.get("version", "?")
    print(f"Bridge health version={ver} paper_force={health.get('paper_force_min_fill')}")
    if str(ver) < "5.5.8":
        print(f"WARN: deploy v5.5.8+ for burst endpoints (got {ver})")

    if args.wait_for_open:
        wait_until_market_open(env, after_open_sec=args.after_open_sec)

    clock = market_clock(env)
    if clock:
        print(f"Market is_open={clock.get('is_open')}")

    total = max(1, args.count)
    batch = max(1, args.batch_size)
    filled_total = 0
    attempt_num = 0

    log_line(f"START burst count={total} interval={args.interval}s base={base_url}", log_file)

    remaining = total
    while remaining > 0:
        n = min(batch, remaining)
        spy = latest_spy(env)
        if batch <= 1:
            data = run_single(base_url, secret, spy, args.timeout)
            attempt_num += 1
            oid = data.get("order_id") or "—"
            status = data.get("status") or data.get("message", "?")
            filled = bool(data.get("filled") or data.get("success"))
            if filled:
                filled_total += 1
            log_line(
                f"#{attempt_num} order={str(oid)[:12]} status={status} filled={'YES' if filled else 'NO'}",
                log_file,
            )
        else:
            data = run_batch_burst(base_url, secret, spy, n, args.interval, args.timeout)
            attempts = data.get("attempts") or []
            batch_filled = int(data.get("filled_count") or 0)
            filled_total += batch_filled
            for att in attempts:
                attempt_num += 1
                oid = att.get("order_id") or "—"
                status = att.get("status") or att.get("message", "?")
                filled = bool(att.get("filled"))
                log_line(
                    f"#{attempt_num} order={str(oid)[:12]} status={status} filled={'YES' if filled else 'NO'}",
                    log_file,
                )
            if not attempts:
                attempt_num += n
                log_line(f"batch HTTP fail: {data.get('message', '?')}", log_file)

        remaining -= n
        if remaining > 0 and args.interval > 0:
            time.sleep(args.interval)

    log_line(f"DONE filled={filled_total}/{total}", log_file)
    print(f"\nRESULT: {filled_total}/{total} filled — log: {log_file}")
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from team_email import notify_burst_complete  # noqa: E402

        notify_burst_complete(filled_total, total)
    except Exception:
        pass
    return 0 if filled_total > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
