#!/usr/bin/env python3
"""Prove each layer of Phase 0 trigger chain (not burst-only). Writes Desktop report + stdout."""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
RENDER = "https://spy-options-bridge.onrender.com"
DESKTOP_REPORT = Path(r"C:\Users\Shiel\Desktop\TRIGGER-CHAIN-PROOF.txt")
ET = ZoneInfo("America/New_York")
TV_TIMEOUT_SEC = 3.5


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV_PATH.is_file():
        return out
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def production_entry_body(env: dict[str, str], signal_price: float) -> dict:
    return {
        "webhookSecret": env.get("WEBHOOK_SECRET", ""),
        "ticker": "SPY",
        "action": "PUT_CREDIT_SPREAD",
        "signalPrice": signal_price,
        "dteFilter": "weekly",
        "strikeOffsetShort": -5,
        "strikeOffsetLong": -6,
        "quantity": 1,
        "fillMode": "aggressive",
        "limitCredit": 0.55,
    }


def production_warning_body(env: dict[str, str], signal_price: float, *, override: bool) -> dict:
    return {
        "webhookSecret": env.get("WEBHOOK_SECRET", ""),
        "ticker": "SPY",
        "signalPrice": signal_price,
        "strikeOffsetShort": -5,
        "strikeOffsetLong": -6,
        "overrideAutoClose": override,
    }


def alpaca_headers(env: dict[str, str]) -> dict[str, str]:
    return {
        "Apca-Api-Key-Id": env.get("APCA_API_KEY_ID", ""),
        "Apca-Api-Secret-Key": env.get("APCA_API_SECRET_KEY", ""),
    }


def alpaca_base(env: dict[str, str]) -> str:
    return (env.get("APCA_API_BASE_URL") or "https://paper-api.alpaca.markets").rstrip("/")


class Log:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.pass_n = 0
        self.fail_n = 0
        self.warn_n = 0

    def ok(self, name: str, detail: str) -> None:
        self._write(f"PASS {name}: {detail}")
        self.pass_n += 1

    def fail(self, name: str, detail: str) -> None:
        self._write(f"FAIL {name}: {detail}")
        self.fail_n += 1

    def warn(self, name: str, detail: str) -> None:
        self._write(f"WARN {name}: {detail}")
        self.warn_n += 1

    def info(self, msg: str) -> None:
        self._write(msg)

    def _write(self, msg: str) -> None:
        line = f"[{datetime.now(ET).strftime('%H:%M:%S')}] {msg}"
        print(line)
        self.lines.append(line)


def timed_post(url: str, body: dict, *, timeout: float) -> tuple[float, int, dict]:
    t0 = time.perf_counter()
    r = httpx.post(url, json=body, timeout=timeout)
    elapsed = time.perf_counter() - t0
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:200]}
    return elapsed, r.status_code, data


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--round-trip",
        action="store_true",
        help="One /exercise/entry fill attempt (paper order; use sparingly)",
    )
    args = parser.parse_args(argv)

    log = Log()
    env = load_env()
    secret = env.get("WEBHOOK_SECRET", "")
    log.info("=== TRIGGER CHAIN PROOF (Phase 0) ===")
    log.info(f"Render: {RENDER}")

    # 1 Render health
    try:
        r = httpx.get(f"{RENDER}/health", timeout=45)
        if r.is_success:
            j = r.json()
            log.ok("render_health", f"v{j.get('version')} configured={j.get('configured')}")
        else:
            log.fail("render_health", f"HTTP {r.status_code}")
    except Exception as exc:
        log.fail("render_health", str(exc))

    # 2 Ping (warm)
    try:
        t0 = time.perf_counter()
        r = httpx.get(f"{RENDER}/ping", timeout=30)
        ms = (time.perf_counter() - t0) * 1000
        if r.is_success:
            log.ok("render_ping", f"{ms:.0f}ms")
        else:
            log.fail("render_ping", f"HTTP {r.status_code}")
    except Exception as exc:
        log.fail("render_ping", str(exc))

    # 3 Bad secret
    if secret:
        _, code, _ = timed_post(
            f"{RENDER}/entry",
            {"webhookSecret": "wrong", "ticker": "SPY"},
            timeout=15,
        )
        if code == 401:
            log.ok("entry_auth_reject", "HTTP 401 on bad secret")
        else:
            log.fail("entry_auth_reject", f"expected 401 got {code}")
    else:
        log.warn("entry_auth_reject", "WEBHOOK_SECRET missing in .env")

    # 4 Production-shaped /entry (must answer <3s for TV)
    if secret:
        body = production_entry_body(env, 590.0)
        elapsed, code, data = timed_post(f"{RENDER}/entry", body, timeout=90)
        if code == 200 and elapsed <= TV_TIMEOUT_SEC:
            proc = data.get("processing") or data.get("success")
            log.ok(
                "entry_webhook_tv_speed",
                f"{elapsed:.2f}s HTTP 200 processing={proc}",
            )
        elif code == 200:
            log.warn(
                "entry_webhook_tv_speed",
                f"{elapsed:.2f}s > {TV_TIMEOUT_SEC}s — TV may TIMEOUT (Render slow)",
            )
        else:
            log.fail("entry_webhook_tv_speed", f"HTTP {code} {elapsed:.2f}s")
    else:
        log.fail("entry_webhook_tv_speed", "WEBHOOK_SECRET missing")

    # 5 Warning notify-only (must answer <3s)
    if secret:
        body = production_warning_body(env, 590.0, override=True)
        elapsed, code, data = timed_post(f"{RENDER}/warning", body, timeout=90)
        if code == 200 and elapsed <= TV_TIMEOUT_SEC:
            log.ok(
                "warning_webhook_tv_speed",
                f"{elapsed:.2f}s action={data.get('action_taken', '?')}",
            )
        elif code == 200:
            log.warn("warning_webhook_tv_speed", f"{elapsed:.2f}s > {TV_TIMEOUT_SEC}s")
        else:
            log.fail("warning_webhook_tv_speed", f"HTTP {code}")
    else:
        log.fail("warning_webhook_tv_speed", "WEBHOOK_SECRET missing")

    # 6 Alpaca account + positions
    h = alpaca_headers(env)
    if h["Apca-Api-Key-Id"]:
        try:
            acc = httpx.get(f"{alpaca_base(env)}/v2/account", headers=h, timeout=25).json()
            pos = httpx.get(f"{alpaca_base(env)}/v2/positions", headers=h, timeout=25).json()
            log.ok(
                "alpaca_read",
                f"equity={acc.get('equity')} positions={len(pos)}",
            )
            acts = httpx.get(
                f"{alpaca_base(env)}/v2/account/activities",
                headers=h,
                params={"page_size": 20},
                timeout=25,
            ).json()
            fills = [a for a in acts if a.get("activity_type") == "FILL"]
            log.info(f"alpaca_recent_fills_in_page: {len(fills)} (see Activities tab)")
            if not fills:
                log.warn(
                    "alpaca_activity",
                    "no FILL rows in last page — flat is normal if no MACD entry today",
                )
            else:
                log.ok("alpaca_activity", f"latest FILL {fills[0].get('transaction_time', '')[:19]}")
        except Exception as exc:
            log.fail("alpaca_read", str(exc))
    else:
        log.fail("alpaca_read", "missing Alpaca keys")

    # 7 TradingView MACD (human path — cannot auto-prove)
    log.warn(
        "tv_macd_live",
        "NOT automated — requires Bullish/Bearish alert fire on chart; check TV alert log",
    )

    if args.round_trip:
        log.info("--- optional round-trip /exercise/entry ---")
        sys.path.insert(0, str(ROOT / "scripts"))
        from paper_pnl_audit import run_exercise_entry  # noqa: E402

        entry = run_exercise_entry(env, base_url=RENDER, timeout=180)
        if entry.get("filled"):
            log.ok("exercise_entry_fill", str(entry.get("order_id", ""))[:12])
        else:
            log.fail("exercise_entry_fill", str(entry.get("message", entry))[:80])

    summary = (
        f"\n{'=' * 50}\n"
        f"PASS={log.pass_n} FAIL={log.fail_n} WARN={log.warn_n}\n"
        "LIVE MACD proof = TV alert 'delivered' + Activities fill on cross.\n"
        "Burst/scenario fills != MACD path.\n"
    )
    log.lines.append(summary.strip())
    print(summary)

    text = "\n".join(log.lines) + "\n"
    DESKTOP_REPORT.write_text(text, encoding="utf-8")
    print(f"WROTE {DESKTOP_REPORT}")
    return 0 if log.fail_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
