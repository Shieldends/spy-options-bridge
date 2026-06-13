#!/usr/bin/env python3
"""Shared helpers for the STX watcher/kill scripts.

Read-only market helpers, OCC symbol utilities (copied from main.py so importing
this module never boots the FastAPI app), and the Grok risk-matrix recommendation
engine. Nothing in this module ever submits an order.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
STATE_PATH = ROOT / "backend-config" / "stx_watcher_state.json"
DATA_BASE = "https://data.alpaca.markets"
ET = ZoneInfo("America/New_York")

# Grok risk-matrix defaults (overridable via StxConfig).
TARGET_OFFSET = 0.10      # buy-to-close limit = midpoint + $0.10
CHASE_CAP_OFFSET = 0.15   # if ask > midpoint + $0.15 -> no limit, force market
SPREAD_ABORT = 0.25       # bid-ask width >= $0.25 -> fast-market abort
MOVE_ABORT_PCT = 3.5      # underlying |move| >= 3.5% within 15 min -> abort
IV_ABORT_POINTS = 8.0     # IV spike >= 8 pts from prev close -> abort
MOVE_WINDOW_SEC = 15 * 60

# Opening-bell cooldown: suppress ONLY the spread-abort trigger 9:30-9:45 ET.
COOLDOWN_START = (9, 30)
COOLDOWN_END = (9, 45)


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV_PATH.exists():
        return out
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
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
    return (env.get("APCA_API_BASE_URL") or "https://paper-api.alpaca.markets").rstrip("/")


def is_paper(env: dict[str, str]) -> bool:
    return "paper-api.alpaca.markets" in alpaca_base(env)


# ── OCC helpers (copied verbatim from main.py to avoid importing the app) ──────
def format_occ_symbol(underlying: str, expiration: str, option_type: str, strike: float) -> str:
    exp = datetime.strptime(expiration, "%Y-%m-%d")
    side = "C" if option_type == "call" else "P"
    return f"{underlying.upper()}{exp.strftime('%y%m%d')}{side}{int(round(strike * 1000)):08d}"


def parse_occ_symbol(symbol: str) -> dict[str, Any] | None:
    """Parse compact OCC e.g. STX260515P00230000."""
    sym = symbol.strip().upper()
    idx = next((i for i, ch in enumerate(sym) if ch.isdigit()), None)
    if idx is None or idx == 0 or len(sym) - idx < 15:
        return None
    underlying = sym[:idx]
    rest = sym[idx:]
    try:
        exp = datetime.strptime(rest[:6], "%y%m%d").strftime("%Y-%m-%d")
        option_type = "call" if rest[6] == "C" else "put"
        strike = int(rest[7:15]) / 1000.0
    except (ValueError, IndexError):
        return None
    return {
        "underlying": underlying,
        "expiration": exp,
        "option_type": option_type,
        "strike": strike,
    }


# ── Config + data models ──────────────────────────────────────────────────────
@dataclass
class StxConfig:
    underlying: str
    expiration: str        # YYYY-MM-DD
    option_type: str       # "put" | "call"
    strike: float
    poll_seconds: int = 15
    prev_close_iv: float | None = None  # decimal vol, e.g. 0.45; enables IV abort
    target_offset: float = TARGET_OFFSET
    chase_cap_offset: float = CHASE_CAP_OFFSET
    spread_abort: float = SPREAD_ABORT
    move_abort_pct: float = MOVE_ABORT_PCT
    iv_abort_points: float = IV_ABORT_POINTS

    @property
    def short_symbol(self) -> str:
        return format_occ_symbol(self.underlying, self.expiration, self.option_type, self.strike)


@dataclass
class MarketSnapshot:
    ts: str
    underlying_price: float | None
    underlying_move_pct: float | None
    bid: float | None
    ask: float | None
    midpoint: float | None
    spread: float | None
    iv: float | None
    iv_delta_points: float | None
    position_open: bool


@dataclass
class Recommendation:
    action: str            # "hold" | "limit" | "market"
    limit_price: float | None
    reasons: list[str] = field(default_factory=list)
    chase_blocked: bool = False


# ── Read-only market fetches ──────────────────────────────────────────────────
def fetch_positions(
    client: httpx.Client, env: dict[str, str]
) -> tuple[list[dict[str, Any]], bool]:
    """Return (positions, ok). ok=False on any transient/API error so callers can
    distinguish a genuine empty book from a failed fetch (never silently exit)."""
    try:
        r = client.get(f"{alpaca_base(env)}/v2/positions", headers=alpaca_headers(env), timeout=30)
        if not r.is_success:
            return [], False
        body = r.json()
        return (body, True) if isinstance(body, list) else ([], False)
    except (httpx.HTTPError, ValueError, KeyError):
        return [], False


def underlying_legs(positions: list[dict[str, Any]], underlying: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in positions:
        meta = parse_occ_symbol(str(p.get("symbol", "")))
        if meta and meta["underlying"] == underlying.upper():
            out.append(p)
    return out


def fetch_underlying_price(client: httpx.Client, env: dict[str, str], underlying: str) -> float | None:
    h = alpaca_headers(env)
    for url in (
        f"{DATA_BASE}/v2/stocks/{underlying}/quotes/latest",
        f"{DATA_BASE}/v2/stocks/{underlying}/trades/latest",
    ):
        try:
            r = client.get(url, headers=h, timeout=20)
            if not r.is_success:
                continue
            j = r.json()
            price = j.get("quote", {}).get("ap") or j.get("trade", {}).get("p")
            if price:
                return float(price)
        except (httpx.HTTPError, ValueError, KeyError):
            continue
    return None


def fetch_option_snapshot(
    client: httpx.Client, env: dict[str, str], occ_symbol: str
) -> dict[str, Any] | None:
    """Options snapshot (beta) for a single OCC symbol -> latest quote + greeks/IV.

    Mirrors main.py's `?symbols=` pattern (targeted request, no pagination risk).
    Returns None if unavailable.
    """
    url = f"{DATA_BASE}/v1beta1/options/snapshots"
    try:
        r = client.get(
            url, headers=alpaca_headers(env), params={"symbols": occ_symbol}, timeout=20
        )
        if not r.is_success:
            return None
        body = r.json()
        snaps = body.get("snapshots") or body
        if not isinstance(snaps, dict):
            return None
        snap = snaps.get(occ_symbol) or snaps.get(occ_symbol.upper())
        return snap if isinstance(snap, dict) else None
    except (httpx.HTTPError, ValueError, KeyError):
        return None


def in_opening_cooldown(now_et: datetime) -> bool:
    start = now_et.replace(hour=COOLDOWN_START[0], minute=COOLDOWN_START[1], second=0, microsecond=0)
    end = now_et.replace(hour=COOLDOWN_END[0], minute=COOLDOWN_END[1], second=0, microsecond=0)
    return start <= now_et < end


def build_recommendation(
    snap: MarketSnapshot, cfg: StxConfig, *, now_et: datetime
) -> Recommendation:
    reasons: list[str] = []

    if not snap.position_open:
        return Recommendation(action="hold", limit_price=None, reasons=["position_closed"])

    abort = False

    # Fast-market abort: underlying move.
    if snap.underlying_move_pct is not None and abs(snap.underlying_move_pct) >= cfg.move_abort_pct:
        abort = True
        reasons.append(f"underlying_move_{snap.underlying_move_pct:.2f}pct>={cfg.move_abort_pct}")

    # Fast-market abort: IV spike (only if a previous-close baseline is available).
    if snap.iv_delta_points is not None and snap.iv_delta_points >= cfg.iv_abort_points:
        abort = True
        reasons.append(f"iv_spike_{snap.iv_delta_points:.1f}pts>={cfg.iv_abort_points}")
    elif cfg.prev_close_iv is None:
        reasons.append("iv_abort_disabled_no_prev_close")

    # Fast-market abort: spread widening — suppressed during 9:30-9:45 ET cooldown.
    if snap.spread is not None and snap.spread >= cfg.spread_abort:
        if in_opening_cooldown(now_et):
            reasons.append(f"spread_{snap.spread:.2f}_suppressed_opening_cooldown")
        else:
            abort = True
            reasons.append(f"spread_{snap.spread:.2f}>={cfg.spread_abort}")

    if abort:
        return Recommendation(action="market", limit_price=None, reasons=reasons)

    if snap.midpoint is None or snap.ask is None:
        return Recommendation(action="hold", limit_price=None, reasons=reasons + ["no_quote"])

    # Chase cap: if ask too far above mid, a limit won't fill -> require market force close.
    if snap.ask > snap.midpoint + cfg.chase_cap_offset:
        reasons.append(
            f"chase_block_ask_{snap.ask:.2f}>mid+{cfg.chase_cap_offset:.2f}"
        )
        return Recommendation(action="market", limit_price=None, reasons=reasons, chase_blocked=True)

    limit_price = round(snap.midpoint + cfg.target_offset, 2)
    reasons.append(f"limit_mid_{snap.midpoint:.2f}+{cfg.target_offset:.2f}")
    return Recommendation(action="limit", limit_price=limit_price, reasons=reasons)


# ── State hand-off (watcher writes, kill reads) ───────────────────────────────
def write_state(cfg: StxConfig, snap: MarketSnapshot, rec: Recommendation) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "symbol": cfg.short_symbol,
        "config": asdict(cfg),
        "snapshot": asdict(snap),
        "recommendation": asdict(rec),
        "written_at": datetime.now(ET).isoformat(timespec="seconds"),
    }
    STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_state() -> dict[str, Any] | None:
    if not STATE_PATH.exists():
        return None
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None
