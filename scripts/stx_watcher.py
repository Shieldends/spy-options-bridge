#!/usr/bin/env python3
"""STX background watcher — read-only verification + recommendation hand-off.

Polls the underlying, the short option leg quote, and (when available) option IV,
then writes a recommendation to backend-config/stx_watcher_state.json for
stx_kill.py to consume. THIS SCRIPT NEVER SUBMITS AN ORDER.

Run (Windows): launchers\\START-STX-WATCHER.bat  (pythonw.exe, headless)
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import deque
from datetime import datetime

import httpx

from stx_common import (
    ET,
    MarketSnapshot,
    StxConfig,
    alpaca_base,
    build_recommendation,
    fetch_option_snapshot,
    fetch_positions,
    fetch_underlying_price,
    is_paper,
    load_env,
    underlying_legs,
    write_state,
)

MOVE_WINDOW_SEC = 15 * 60
EMPTY_EXIT_POLLS = 3  # consecutive confirmed-empty polls before declaring position closed


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="STX read-only watcher")
    p.add_argument("--underlying", default="STX")
    p.add_argument("--expiration", required=True, help="YYYY-MM-DD")
    p.add_argument("--type", dest="option_type", choices=["put", "call"], default="put")
    p.add_argument("--strike", type=float, required=True)
    p.add_argument("--poll", type=int, default=15, help="poll interval seconds")
    p.add_argument("--prev-close-iv", type=float, default=None, help="decimal vol; enables IV abort")
    p.add_argument("--once", action="store_true", help="single pass then exit (for tests)")
    return p.parse_args()


def midpoint_of(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    return round((bid + ask) / 2.0, 4)


def extract_quote(snap: dict | None) -> tuple[float | None, float | None, float | None]:
    """Return (bid, ask, iv) from an Alpaca options snapshot dict."""
    if not snap:
        return None, None, None
    quote = snap.get("latestQuote", {})
    bid = quote.get("bp")
    ask = quote.get("ap")
    greeks = snap.get("greeks", {}) or {}
    iv = snap.get("impliedVolatility") or greeks.get("impliedVolatility")
    bid_f = float(bid) if bid not in (None, 0) else None
    ask_f = float(ask) if ask not in (None, 0) else None
    iv_f = float(iv) if iv is not None else None
    return bid_f, ask_f, iv_f


def main() -> int:
    args = parse_args()
    cfg = StxConfig(
        underlying=args.underlying,
        expiration=args.expiration,
        option_type=args.option_type,
        strike=args.strike,
        poll_seconds=args.poll,
        prev_close_iv=args.prev_close_iv,
    )

    env = load_env()
    if not is_paper(env):
        print(f"FAIL refusing non-paper base: {alpaca_base(env)}", flush=True)
        return 1
    if not (env.get("APCA_API_KEY_ID") or env.get("ALPACA_API_KEY")):
        print("FAIL Alpaca credentials missing in .env", flush=True)
        return 1

    print(f"STX watcher armed for {cfg.short_symbol} (poll={cfg.poll_seconds}s)", flush=True)
    price_window: deque[tuple[float, float]] = deque()  # (epoch, price)
    empty_streak = 0

    with httpx.Client() as client:
        while True:
            now = datetime.now(ET)
            now_epoch = time.time()

            positions, fetch_ok = fetch_positions(client, env)
            legs = underlying_legs(positions, cfg.underlying)
            position_open = len(legs) > 0
            if not fetch_ok:
                # Transient API error: do NOT treat as closed; keep last-known state.
                position_open = True
                empty_streak = 0
            elif position_open:
                empty_streak = 0
            else:
                empty_streak += 1

            price = fetch_underlying_price(client, env, cfg.underlying)
            move_pct: float | None = None
            if price is not None:
                price_window.append((now_epoch, price))
                while price_window and now_epoch - price_window[0][0] > MOVE_WINDOW_SEC:
                    price_window.popleft()
                ref = price_window[0][1]
                if ref:
                    move_pct = round((price - ref) / ref * 100.0, 3)

            snap_raw = fetch_option_snapshot(client, env, cfg.short_symbol)
            bid, ask, iv = extract_quote(snap_raw)
            mid = midpoint_of(bid, ask)
            spread = round(ask - bid, 4) if (bid is not None and ask is not None) else None
            iv_delta = None
            if iv is not None and cfg.prev_close_iv is not None:
                iv_delta = round((iv - cfg.prev_close_iv) * 100.0, 2)  # vol points

            snapshot = MarketSnapshot(
                ts=now.isoformat(timespec="seconds"),
                underlying_price=price,
                underlying_move_pct=move_pct,
                bid=bid,
                ask=ask,
                midpoint=mid,
                spread=spread,
                iv=iv,
                iv_delta_points=iv_delta,
                position_open=position_open,
            )
            rec = build_recommendation(snapshot, cfg, now_et=now)
            write_state(cfg, snapshot, rec)

            print(
                f"[{snapshot.ts}] open={position_open} fetch_ok={fetch_ok} px={price} "
                f"move%={move_pct} mid={mid} spread={spread} iv={iv} -> {rec.action} "
                f"limit={rec.limit_price} ({';'.join(rec.reasons)})",
                flush=True,
            )

            if args.once:
                return 0
            if fetch_ok and empty_streak >= EMPTY_EXIT_POLLS:
                print(
                    f"Position closed ({empty_streak} consecutive empty polls) — watcher exiting.",
                    flush=True,
                )
                return 0
            time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
