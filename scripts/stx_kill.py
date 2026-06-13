#!/usr/bin/env python3
"""STX interactive kill — operator-gated position flattening.

Reads the watcher's state (config + last recommendation + rolling underlying-move),
re-polls a fresh quote, then builds an up-to-date recommendation. The watcher's
underlying-move % is carried into the fresh snapshot so the move abort is NOT
silently disabled in this single-shot tool, and the watcher's own recommendation
is displayed alongside the fresh one. Then HALTS at a raw input() gate requiring a
manual 'Y'/Enter before any live order is transmitted. Defaults to --dry-run.

Run (Windows): launchers\\STX-KILL.bat  (python.exe, interactive terminal)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from typing import Any

import httpx

from stx_common import (
    ET,
    MarketSnapshot,
    Recommendation,
    StxConfig,
    alpaca_base,
    alpaca_headers,
    build_recommendation,
    fetch_option_snapshot,
    fetch_positions,
    fetch_underlying_price,
    is_paper,
    load_env,
    read_state,
    underlying_legs,
)
from stx_watcher import extract_quote, midpoint_of

CRITICAL_LINE = (
    "[CRITICAL] OPERATIONAL RULES VALIDATED. "
    "SYSTEM RECOMMENDS IMMEDIATE POSITION FLATTENING."
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="STX operator-gated kill")
    p.add_argument("--execute", action="store_true", help="actually send orders (default: dry-run)")
    return p.parse_args()


def cfg_from_state(state: dict[str, Any]) -> StxConfig:
    c = state["config"]
    return StxConfig(
        underlying=c["underlying"],
        expiration=c["expiration"],
        option_type=c["option_type"],
        strike=float(c["strike"]),
        poll_seconds=int(c.get("poll_seconds", 15)),
        prev_close_iv=c.get("prev_close_iv"),
    )


def fresh_snapshot(
    client: httpx.Client, env: dict[str, str], cfg: StxConfig, *, prior_move_pct: float | None
) -> tuple[MarketSnapshot, list[dict[str, Any]], bool]:
    now = datetime.now(ET)
    positions, fetch_ok = fetch_positions(client, env)
    legs = underlying_legs(positions, cfg.underlying)
    price = fetch_underlying_price(client, env, cfg.underlying)
    bid, ask, iv = extract_quote(fetch_option_snapshot(client, env, cfg.short_symbol))
    mid = midpoint_of(bid, ask)
    spread = round(ask - bid, 4) if (bid is not None and ask is not None) else None
    iv_delta = (
        round((iv - cfg.prev_close_iv) * 100.0, 2)
        if (iv is not None and cfg.prev_close_iv is not None)
        else None
    )
    snap = MarketSnapshot(
        ts=now.isoformat(timespec="seconds"),
        underlying_price=price,
        underlying_move_pct=prior_move_pct,  # carried from the watcher's rolling window
        bid=bid,
        ask=ask,
        midpoint=mid,
        spread=spread,
        iv=iv,
        iv_delta_points=iv_delta,
        position_open=len(legs) > 0,
    )
    return snap, legs, fetch_ok


def close_position_at_market(
    client: httpx.Client, base: str, h: dict[str, str], symbol: str, *, dry_run: bool
) -> None:
    if dry_run:
        print(f"  DRY-RUN would MARKET close (DELETE /v2/positions/{symbol})")
        return
    r = client.delete(f"{base}/v2/positions/{symbol}", headers=h, timeout=30)
    print(f"  MARKET close {symbol} -> HTTP {r.status_code} {r.text[:200]}")


def buy_to_close_limit(
    client: httpx.Client,
    base: str,
    h: dict[str, str],
    symbol: str,
    qty: int,
    limit_price: float,
    *,
    dry_run: bool,
) -> None:
    order = {
        "symbol": symbol,
        "qty": str(qty),
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "limit_price": str(limit_price),
        "position_intent": "buy_to_close",
    }
    if dry_run:
        print(f"  DRY-RUN would POST /v2/orders {order}")
        return
    r = client.post(f"{base}/v2/orders", headers=h, json=order, timeout=30)
    print(f"  LIMIT buy_to_close {symbol} @ {limit_price} -> HTTP {r.status_code} {r.text[:200]}")


def short_leg_qty(legs: list[dict[str, Any]], cfg: StxConfig) -> int:
    for leg in legs:
        if str(leg.get("symbol", "")).upper() == cfg.short_symbol:
            return abs(int(float(leg.get("qty", "0"))))
    return 0


def main() -> int:
    args = parse_args()
    dry_run = not args.execute

    state = read_state()
    if not state:
        print("FAIL no watcher state found — start stx_watcher.py first.")
        return 1
    cfg = cfg_from_state(state)

    env = load_env()
    if not is_paper(env):
        print(f"FAIL refusing non-paper base: {alpaca_base(env)}")
        return 1
    if not (env.get("APCA_API_KEY_ID") or env.get("ALPACA_API_KEY")):
        print("FAIL Alpaca credentials missing in .env")
        return 1

    base = alpaca_base(env)
    h = alpaca_headers(env)

    state_snap = state.get("snapshot", {})
    prior_move_pct = state_snap.get("underlying_move_pct") if isinstance(state_snap, dict) else None
    watcher_rec = state.get("recommendation", {})

    with httpx.Client() as client:
        snap, legs, fetch_ok = fresh_snapshot(client, env, cfg, prior_move_pct=prior_move_pct)
        if not fetch_ok:
            print("FAIL could not fetch positions (API error) — aborting for safety.")
            return 1
        rec: Recommendation = build_recommendation(snap, cfg, now_et=datetime.now(ET))

        print("=" * 64)
        print(f"STX KILL — {cfg.short_symbol}   (mode: {'DRY-RUN' if dry_run else 'LIVE'})")
        print(f"  underlying={snap.underlying_price} bid={snap.bid} ask={snap.ask} "
              f"mid={snap.midpoint} spread={snap.spread} iv={snap.iv} "
              f"move%={snap.underlying_move_pct} (carried from watcher)")
        print(f"  open legs: {[leg.get('symbol') for leg in legs]}")
        if isinstance(watcher_rec, dict) and watcher_rec:
            print(f"  watcher rec (from state): {str(watcher_rec.get('action')).upper()} "
                  f"limit={watcher_rec.get('limit_price')} @ {state.get('written_at')}")
        print(f"  fresh recommendation: {rec.action.upper()}  limit={rec.limit_price}")
        print(f"  reasons: {';'.join(rec.reasons)}")
        if rec.chase_blocked:
            print("  WARNING: chase cap exceeded — limit NOT allowed; MARKET force close required.")
        print("=" * 64)

        if not snap.position_open or rec.action == "hold":
            print("Nothing to flatten (position closed or HOLD). Exiting without prompt.")
            return 0

        # Mandatory operator gate — the ONLY place a live order can be transmitted.
        print(CRITICAL_LINE)
        answer = input("Confirm position flattening? [Y/Enter to proceed, anything else aborts]: ")
        if answer.strip() not in ("", "Y", "y"):
            print("Aborted by operator. No orders sent.")
            return 0

        if rec.action == "market":
            for leg in legs:
                close_position_at_market(client, base, h, str(leg.get("symbol")), dry_run=dry_run)
        elif rec.action == "limit" and rec.limit_price is not None:
            qty = short_leg_qty(legs, cfg)
            if qty <= 0:
                print("FAIL short leg not found in open positions.")
                return 1
            buy_to_close_limit(
                client, base, h, cfg.short_symbol, qty, rec.limit_price, dry_run=dry_run
            )

        print("Done." if not dry_run else "Dry-run complete — no orders sent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
