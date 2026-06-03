"""
spy-options-bridge v5.5.5 — ALPACA PAPER (default broker)

TradingView webhook → Render → Alpaca multi-leg SPY put credit spreads.

>>> DEPLOY THIS FILE TO GITHUB / RENDER:
>>>   C:\\Users\\Shiel\\spy-options-bridge\\main.py
>>> NOT app\\main.py (old Tastytrade module — not used by Render)

Render start command:  uvicorn main:app --host 0.0.0.0 --port $PORT

Required Render env vars:
  BROKER=alpaca
  APCA_API_KEY_ID=<your paper key>
  APCA_API_SECRET_KEY=<your paper secret>
  APCA_API_BASE_URL=https://paper-api.alpaca.markets
  EXECUTION_MODE=production
  WEBHOOK_SECRET=<your secret>

Endpoints:
  GET  /health   — shows broker=alpaca when configured
  POST /entry    — Alpaca mleg entry + GTC take-profit + GTC stop-loss
  POST /webhook  — alias for /entry
  POST /warning  — danger zone: notify + optional auto-close spread (override in JSON)
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from enum import Enum
from functools import lru_cache
from math import floor
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
CERT_URL = "https://api.cert.tastyworks.com"
ALPACA_PAPER_URL = "https://paper-api.alpaca.markets"

# ── Settings ──────────────────────────────────────────────────────────────────


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    webhook_secret: str = Field(default="", alias="WEBHOOK_SECRET")
    execution_mode: str = Field(default="production", alias="EXECUTION_MODE")
    broker: str = Field(default="alpaca", alias="BROKER")

    apca_api_base_url: str = Field(default=ALPACA_PAPER_URL, alias="APCA_API_BASE_URL")
    apca_api_key_id: str = Field(default="", alias="APCA_API_KEY_ID")
    apca_api_secret_key: str = Field(default="", alias="APCA_API_SECRET_KEY")
    alpaca_api_key: str = Field(default="", alias="ALPACA_API_KEY")
    alpaca_secret_key: str = Field(default="", alias="ALPACA_SECRET_KEY")

    tastytrade_api_base_url: str = Field(default=CERT_URL, alias="TASTYTRADE_API_BASE_URL")
    tastytrade_username: str = Field(default="", alias="TASTYTRADE_USERNAME")
    tastytrade_password: str = Field(default="", alias="TASTYTRADE_PASSWORD")
    tastytrade_account_number: str = Field(default="", alias="TASTYTRADE_ACCOUNT_NUMBER")
    tastytrade_sandbox_username: str = Field(default="", alias="TASTYTRADE_SANDBOX_USERNAME")
    tastytrade_sandbox_password: str = Field(default="", alias="TASTYTRADE_SANDBOX_PASSWORD")

    auto_take_profit: bool = Field(default=True, alias="AUTO_TAKE_PROFIT")
    take_profit_pct: float = Field(default=0.50, alias="TAKE_PROFIT_PCT")
    auto_stop_loss: bool = Field(default=True, alias="AUTO_STOP_LOSS")
    stop_loss_multiplier: float = Field(default=2.0, alias="STOP_LOSS_MULTIPLIER")
    danger_zone_pct: float = Field(default=0.01, alias="DANGER_ZONE_PCT")
    auto_close_on_warning: bool = Field(default=True, alias="AUTO_CLOSE_ON_WARNING")
    warning_close_multiplier: float = Field(default=1.2, alias="WARNING_CLOSE_MULTIPLIER")
    warning_cancel_resting_exits: bool = Field(default=True, alias="WARNING_CANCEL_RESTING_EXITS")
    default_dte_filter: str = Field(default="weekly", alias="DEFAULT_DTE_FILTER")
    alpaca_exit_fill_timeout: int = Field(default=600, alias="ALPACA_EXIT_FILL_TIMEOUT")
    alpaca_exit_poll_seconds: float = Field(default=3.0, alias="ALPACA_EXIT_POLL_SECONDS")
    auto_chase_entry_fill: bool = Field(default=True, alias="AUTO_CHASE_ENTRY_FILL")
    entry_chase_wait_seconds: float = Field(default=2.0, alias="ENTRY_CHASE_WAIT_SECONDS")
    entry_chase_poll_seconds: float = Field(default=1.5, alias="ENTRY_CHASE_POLL_SECONDS")
    entry_chase_max_attempts: int = Field(default=20, alias="ENTRY_CHASE_MAX_ATTEMPTS")
    entry_chase_floor_extra_polls: int = Field(default=12, alias="ENTRY_CHASE_FLOOR_EXTRA_POLLS")
    entry_min_credit: float = Field(default=0.05, alias="ENTRY_MIN_CREDIT")
    paper_force_min_fill: bool = Field(default=True, alias="PAPER_FORCE_MIN_FILL")

    discord_webhook_url: str = Field(default="", alias="DISCORD_WEBHOOK_URL")
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    default_underlying: str = Field(default="SPY", alias="DEFAULT_UNDERLYING")
    default_quantity: int = Field(default=1, alias="DEFAULT_QUANTITY")
    default_strike_offset_short: int = Field(default=-5, alias="DEFAULT_STRIKE_OFFSET_SHORT")
    default_strike_offset_long: int = Field(default=-8, alias="DEFAULT_STRIKE_OFFSET_LONG")
    default_limit_credit: float = Field(default=0.35, alias="DEFAULT_LIMIT_CREDIT")
    default_fill_mode: str = Field(default="exercise", alias="DEFAULT_FILL_MODE")
    # fixed | auto | aggressive | exercise | fill — exercise/fill lean low for paper fills
    max_quantity: int = Field(default=0, alias="MAX_QUANTITY")
    # 0 = no cap; set e.g. 10 to limit spreads per alert

    @property
    def is_live(self) -> bool:
        return self.execution_mode.lower() == "production"

    @property
    def use_alpaca(self) -> bool:
        """Alpaca unless BROKER is explicitly set to tastytrade."""
        return self.broker.lower().strip() != "tastytrade"

    @property
    def alpaca_key(self) -> str:
        return self.apca_api_key_id or self.alpaca_api_key

    @property
    def alpaca_secret(self) -> str:
        return self.apca_api_secret_key or self.alpaca_secret_key

    @property
    def alpaca_configured(self) -> bool:
        return bool(self.alpaca_key and self.alpaca_secret)

    @property
    def is_alpaca_paper(self) -> bool:
        return "paper-api.alpaca.markets" in self.apca_api_base_url.lower()

    @property
    def username(self) -> str:
        return self.tastytrade_username or self.tastytrade_sandbox_username

    @property
    def password(self) -> str:
        return self.tastytrade_password or self.tastytrade_sandbox_password

    @property
    def configured(self) -> bool:
        if self.use_alpaca:
            return self.alpaca_configured
        return bool(self.username and self.password and self.tastytrade_account_number)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ── Models ────────────────────────────────────────────────────────────────────


class SpreadStrategy(str, Enum):
    PUT_CREDIT_SPREAD = "put_credit_spread"
    CALL_CREDIT_SPREAD = "call_credit_spread"


class TradingViewSignal(BaseModel):
    ticker: str
    strategy: SpreadStrategy | None = None
    signal_price: float | None = Field(default=None, alias="signalPrice")
    quantity: int = 1
    strike_offset_short: int = Field(default=-5, alias="strikeOffsetShort")
    strike_offset_long: int = Field(default=-8, alias="strikeOffsetLong")
    short_strike: float | None = Field(default=None, alias="short_strike")
    long_strike: float | None = Field(default=None, alias="long_strike")
    limit_credit: float | None = Field(default=None, alias="limitCredit")
    fill_mode: str | None = Field(default=None, alias="fillMode")
    expiration: str = "0dte"
    dte_filter: str | None = Field(default=None, alias="dteFilter")
    action: str = "enter"

    model_config = {"populate_by_name": True}

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper().replace(" ", "")

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, value: str | None) -> str:
        return str(value or "enter").upper()

    @field_validator("quantity", mode="before")
    @classmethod
    def coerce_quantity(cls, value: Any) -> int:
        """Accept int/float from JSON or TradingView {{strategy.order.contracts}}."""
        if value is None or value == "":
            return 1
        qty = int(float(value))
        return max(qty, 1)

    @model_validator(mode="before")
    @classmethod
    def merge_strategy_quantity(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        merged = dict(data)
        for key in ("contracts", "strategyOrderContracts", "strategy_order_contracts"):
            if key in merged and merged.get("quantity") in (None, "", 1):
                merged["quantity"] = merged[key]
                break
        return merged

    @model_validator(mode="after")
    def resolve_strategy(self) -> "TradingViewSignal":
        if self.action in {"PUT_CREDIT_SPREAD", "PUT CREDIT SPREAD"}:
            self.strategy = SpreadStrategy.PUT_CREDIT_SPREAD
        elif self.action in {"CALL_CREDIT_SPREAD", "CALL CREDIT SPREAD"}:
            self.strategy = SpreadStrategy.CALL_CREDIT_SPREAD
        elif self.strategy is None:
            self.strategy = SpreadStrategy.PUT_CREDIT_SPREAD
        if self.short_strike is not None and self.long_strike is not None:
            return self
        if self.signal_price is None:
            raise ValueError("Provide signalPrice OR both short_strike and long_strike")
        return self

    @property
    def uses_explicit_strikes(self) -> bool:
        return self.short_strike is not None and self.long_strike is not None


class WarningSignal(BaseModel):
    ticker: str
    signal_price: float = Field(alias="signalPrice")
    short_strike: float | None = Field(default=None, alias="short_strike")
    long_strike: float | None = Field(default=None, alias="long_strike")
    strike_offset_short: int | None = Field(default=None, alias="strikeOffsetShort")
    strike_offset_long: int | None = Field(default=None, alias="strikeOffsetLong")
    override_auto_close: bool = Field(default=False, alias="overrideAutoClose")
    force_auto_close: bool = Field(default=False, alias="forceAutoClose")
    close_debit: float | None = Field(default=None, alias="closeDebit")

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def resolve_strikes(self) -> "WarningSignal":
        atm = _round_strike(self.signal_price)
        if self.short_strike is None and self.strike_offset_short is not None:
            self.short_strike = atm + self.strike_offset_short
        if self.long_strike is None and self.strike_offset_long is not None:
            self.long_strike = atm + self.strike_offset_long
        if self.short_strike is None:
            raise ValueError("short_strike or strikeOffsetShort required for /warning")
        return self


class SpreadLeg(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    position_intent: str
    ratio_qty: str = "1"


class SpreadPackage(BaseModel):
    qty: str
    legs: list[SpreadLeg]
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrderResult(BaseModel):
    success: bool
    message: str
    dry_run: bool = False
    payload: dict[str, Any] | None = None
    broker_response: dict[str, Any] | None = None


class EntryResponse(BaseModel):
    success: bool
    message: str
    dry_run: bool
    expiration_resolved: str | None = None
    danger_zone: bool = False
    risk_warning: str | None = None
    entry: OrderResult | None = None
    take_profit: OrderResult | None = None
    stop_loss: OrderResult | None = None
    notifications: dict[str, Any] = Field(default_factory=dict)


class WarningResponse(BaseModel):
    danger_zone: bool
    risk_warning: str | None = None
    distance_pct: float | None = None
    action_taken: str = "none"
    survival_odds_expire_otm: float | None = None
    protocol_notes: list[str] = Field(default_factory=list)
    close_order: OrderResult | None = None
    positions_matched: int = 0
    notifications: dict[str, Any] = Field(default_factory=dict)


# ── DTE / Weekly expiration filter ────────────────────────────────────────────


def resolve_dte_expiration(expiration: str, dte_filter: str | None = None, now: datetime | None = None) -> str:
    """
    Dynamic DTE weekly filter.

    Supported values:
      0dte, today, +0 days     → today's date (ET)
      weekly, week, +0 week     → nearest Friday on/after today
      YYYY-MM-DD or YYMMDD      → explicit expiration
    """
    now = now or datetime.now(tz=ET)
    spec = (dte_filter or expiration or "0dte").strip().lower()

    if spec in {"0dte", "today", "+0 days", "+0 day", ""}:
        return now.strftime("%Y-%m-%d")

    if spec in {"weekly", "week", "+0 week", "0dte_weekly"}:
        # Nearest Friday on or after today (standard weekly options cycle)
        weekday = now.weekday()  # Mon=0 … Fri=4
        days_until_friday = (4 - weekday) % 7
        friday = now + timedelta(days=days_until_friday)
        return friday.strftime("%Y-%m-%d")

    if len(spec) == 10 and spec[4] == "-":
        return spec

    if len(spec) == 6 and spec.isdigit():
        return datetime.strptime(spec, "%y%m%d").strftime("%Y-%m-%d")

    raise ValueError(f"Unsupported DTE filter / expiration: {spec}")


# ── Spread builder ────────────────────────────────────────────────────────────


def _round_strike(price: float) -> float:
    return round(floor(price), 2)


def format_occ_symbol(underlying: str, expiration: str, option_type: str, strike: float) -> str:
    exp = datetime.strptime(expiration, "%Y-%m-%d")
    return f"{underlying.upper()}{exp.strftime('%y%m%d')}{'C' if option_type == 'call' else 'P'}{int(round(strike * 1000)):08d}"


def to_tastytrade_symbol(compact: str) -> str:
    idx = next(i for i, ch in enumerate(compact) if ch.isdigit())
    return compact[:idx].ljust(6) + compact[idx:]


def build_spread(signal: TradingViewSignal, settings: Settings) -> SpreadPackage:
    expiration = resolve_dte_expiration(signal.expiration, signal.dte_filter or settings.default_dte_filter)

    if signal.uses_explicit_strikes:
        short_strike = float(signal.short_strike)  # type: ignore[arg-type]
        long_strike = float(signal.long_strike)  # type: ignore[arg-type]
    else:
        atm = _round_strike(signal.signal_price)  # type: ignore[arg-type]
        short_strike = atm + signal.strike_offset_short
        long_strike = atm + signal.strike_offset_long

    option_type = "put" if signal.strategy == SpreadStrategy.PUT_CREDIT_SPREAD else "call"
    limit_credit = signal.limit_credit if signal.limit_credit is not None else settings.default_limit_credit

    short_sym = format_occ_symbol(signal.ticker, expiration, option_type, short_strike)
    long_sym = format_occ_symbol(signal.ticker, expiration, option_type, long_strike)

    return SpreadPackage(
        qty=str(signal.quantity),
        legs=[
            SpreadLeg(symbol=short_sym, side="sell", position_intent="sell_to_open"),
            SpreadLeg(symbol=long_sym, side="buy", position_intent="buy_to_open"),
        ],
        metadata={
            "underlying": signal.ticker,
            "strategy": signal.strategy.value if signal.strategy else "put_credit_spread",
            "expiration": expiration,
            "short_strike": short_strike,
            "long_strike": long_strike,
            "limit_credit": limit_credit,
            "dte_filter": signal.dte_filter or settings.default_dte_filter,
        },
    )


def build_entry_payload(spread: SpreadPackage) -> dict:
    """Tastytrade cert multi-leg entry."""
    credit = float(spread.metadata["limit_credit"])
    qty = int(spread.qty)
    return {
        "time-in-force": "Day",
        "order-type": "Limit",
        "price": f"{credit:.2f}",
        "price-effect": "Credit",
        "legs": [
            {
                "instrument-type": "Equity Option",
                "symbol": to_tastytrade_symbol(spread.legs[0].symbol),
                "quantity": qty,
                "action": "Sell to Open",
            },
            {
                "instrument-type": "Equity Option",
                "symbol": to_tastytrade_symbol(spread.legs[1].symbol),
                "quantity": qty,
                "action": "Buy to Open",
            },
        ],
    }


def format_alpaca_limit_price(amount: float, *, is_credit: bool) -> str:
    """
    Alpaca mleg limit_price sign convention:
      negative = credit received (sell spread)
      positive = debit paid (buy spread back)
    """
    value = round(abs(amount), 2)
    return f"{-value:.2f}" if is_credit else f"{value:.2f}"


async def fetch_alpaca_option_strikes(
    settings: Settings,
    underlying: str,
    expiration: str,
    option_type: str,
) -> list[float]:
    """Return sorted strike prices listed on Alpaca for one expiration."""
    base = settings.apca_api_base_url.rstrip("/")
    headers = {
        "Apca-Api-Key-Id": settings.alpaca_key,
        "Apca-Api-Secret-Key": settings.alpaca_secret,
    }
    params = {
        "underlying_symbols": underlying.upper(),
        "expiration_date": expiration,
        "type": option_type,
        "limit": 1000,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{base}/v2/options/contracts", headers=headers, params=params)
    if not r.is_success:
        logger.warning("Alpaca strike lookup failed (%s): %s", r.status_code, r.text[:200])
        return []

    data = r.json()
    contracts = data.get("option_contracts") or []
    strikes: list[float] = []
    for row in contracts:
        if not isinstance(row, dict):
            continue
        strike = row.get("strike_price")
        if strike is not None:
            strikes.append(float(strike))
    return sorted(set(strikes))


def snap_put_credit_strikes(short_target: float, long_target: float, available: list[float]) -> tuple[float, float]:
    """Map computed strikes to Alpaca-listed puts (long strike must be below short)."""
    if not available:
        return short_target, long_target

    short_candidates = [s for s in available if s <= short_target]
    short = max(short_candidates) if short_candidates else min(available, key=lambda s: abs(s - short_target))

    long_candidates = [s for s in available if s < short and s <= long_target]
    if long_candidates:
        long = max(long_candidates)
    else:
        below_short = [s for s in available if s < short]
        if not below_short:
            raise ValueError(f"No Alpaca put strike below short strike {short}")
        long = max(below_short)

    return short, long


def snap_call_credit_strikes(short_target: float, long_target: float, available: list[float]) -> tuple[float, float]:
    """Map computed strikes to Alpaca-listed calls (long strike must be above short)."""
    if not available:
        return short_target, long_target

    short_candidates = [s for s in available if s >= short_target]
    short = min(short_candidates) if short_candidates else min(available, key=lambda s: abs(s - short_target))

    long_candidates = [s for s in available if s > short and s >= long_target]
    if long_candidates:
        long = min(long_candidates)
    else:
        above_short = [s for s in available if s > short]
        if not above_short:
            raise ValueError(f"No Alpaca call strike above short strike {short}")
        long = min(above_short)

    return short, long


def _normalize_fill_mode(mode: str | None, settings: Settings) -> str:
    raw = (mode or settings.default_fill_mode or "aggressive").strip().lower()
    if raw in {"auto", "market", "mid", "quote"}:
        return "auto"
    if raw in {"aggressive", "fast"}:
        return "aggressive"
    if raw in {"exercise", "expedite", "probe", "system_test", "fill"}:
        return "exercise"
    return "fixed"


def _quote_fallback_credit(cap: float | None, *, floor: float = 0.05) -> float:
    """When bid/ask are missing, start low — chasing will reprice down if needed."""
    return floor


async def fetch_option_snapshot_quotes(settings: Settings, symbols: list[str]) -> dict[str, dict[str, float]]:
    """Latest bid/ask per OCC symbol from Alpaca data API."""
    if not symbols:
        return {}
    headers = {
        "Apca-Api-Key-Id": settings.alpaca_key,
        "Apca-Api-Secret-Key": settings.alpaca_secret,
    }
    params = {"symbols": ",".join(symbols)}
    url = "https://data.alpaca.markets/v1beta1/options/snapshots"
    out: dict[str, dict[str, float]] = {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url, headers=headers, params=params)
    if not r.is_success:
        logger.warning("Option snapshot fetch failed (%s): %s", r.status_code, r.text[:300])
        return out

    body = r.json()
    snapshots = body.get("snapshots") or body
    if not isinstance(snapshots, dict):
        return out

    for sym, snap in snapshots.items():
        if not isinstance(snap, dict):
            continue
        quote = snap.get("latestQuote") or snap.get("latest_quote") or snap
        bid = quote.get("bid_price") or quote.get("bp") or quote.get("bid")
        ask = quote.get("ask_price") or quote.get("ap") or quote.get("ask")
        try:
            out[sym.upper()] = {
                "bid": float(bid) if bid is not None else 0.0,
                "ask": float(ask) if ask is not None else 0.0,
            }
        except (TypeError, ValueError):
            continue
    return out


def estimate_credit_from_quotes(
    spread: SpreadPackage,
    quotes: dict[str, dict[str, float]],
    *,
    mode: str,
    cap: float | None,
) -> tuple[float, dict[str, Any]]:
    """
    Put/call credit spread entry: sell short leg, buy long leg.
    Natural credit ≈ short_bid - long_ask (aggressive leans lower for faster fill).
    """
    short_sym = spread.legs[0].symbol.upper()
    long_sym = spread.legs[1].symbol.upper()
    short_q = quotes.get(short_sym, {})
    long_q = quotes.get(long_sym, {})
    short_bid = short_q.get("bid", 0.0)
    long_ask = long_q.get("ask", 0.0)

    meta: dict[str, Any] = {
        "short_bid": short_bid,
        "long_ask": long_ask,
        "fill_mode": mode,
    }

    if short_bid <= 0 and long_ask <= 0:
        fallback = _quote_fallback_credit(cap)
        meta["quote_source"] = "fallback_no_quotes"
        return fallback, meta

    mid_credit = max((short_bid + short_q.get("ask", short_bid)) / 2 - (long_ask + long_q.get("bid", long_ask)) / 2, 0.05)
    natural = max(short_bid - long_ask, 0.05)
    if mode == "aggressive":
        credit = max(natural * 0.80 - 0.02, 0.05)
        meta["quote_source"] = "bid_ask_aggressive"
    elif mode == "exercise":
        # Paper / validation: price well below market for faster fills.
        credit = max(natural * 0.55 - 0.05, 0.05)
        meta["quote_source"] = "bid_ask_exercise"
    else:
        credit = max(min(natural, mid_credit), 0.05)
        meta["quote_source"] = "bid_ask_auto"

    credit = round(credit, 2)
    if cap is not None and cap > 0:
        credit = min(credit, round(cap, 2))
        meta["cap_applied"] = cap

    return credit, meta


async def resolve_entry_limit_credit(
    settings: Settings,
    spread: SpreadPackage,
    signal: TradingViewSignal,
) -> SpreadPackage:
    mode = _normalize_fill_mode(signal.fill_mode, settings)
    requested = signal.limit_credit if signal.limit_credit is not None else settings.default_limit_credit
    meta = {"fill_mode_resolved": mode, "limit_credit_requested": requested}

    # Alpaca paper: always start at minimum credit so simulator fills (user opts in via env).
    if settings.use_alpaca and settings.is_alpaca_paper and settings.paper_force_min_fill:
        credit = round(settings.entry_min_credit, 2)
        meta["fill_mode_resolved"] = "paper_force_min"
        meta["limit_credit_final"] = credit
        logger.info("Paper force fill: entry credit pinned to $%s", credit)
        return spread_with_credit(
            SpreadPackage(
                qty=spread.qty,
                legs=spread.legs,
                metadata={**spread.metadata, **meta},
            ),
            credit,
        )

    if mode == "fixed" or not settings.use_alpaca:
        credit = float(requested)
        meta["limit_credit_final"] = credit
        return spread_with_credit(
            SpreadPackage(
                qty=spread.qty,
                legs=spread.legs,
                metadata={**spread.metadata, **meta},
            ),
            credit,
        )

    quotes = await fetch_option_snapshot_quotes(settings, [leg.symbol for leg in spread.legs])
    cap = float(requested) if requested else None
    credit, qmeta = estimate_credit_from_quotes(spread, quotes, mode=mode, cap=cap)
    meta.update(qmeta)
    meta["limit_credit_final"] = credit
    logger.info(
        "Fill mode %s: credit $%s (requested $%s) short_bid=%s long_ask=%s",
        mode,
        credit,
        requested,
        meta.get("short_bid"),
        meta.get("long_ask"),
    )
    return spread_with_credit(
        SpreadPackage(
            qty=spread.qty,
            legs=spread.legs,
            metadata={**spread.metadata, **meta},
        ),
        credit,
    )


async def align_spread_to_alpaca(settings: Settings, spread: SpreadPackage, signal: TradingViewSignal) -> SpreadPackage:
    """Snap strikes to Alpaca-listed contracts and rebuild OCC symbols."""
    if signal.uses_explicit_strikes:
        short_strike = float(signal.short_strike)  # type: ignore[arg-type]
        long_strike = float(signal.long_strike)  # type: ignore[arg-type]
    else:
        short_strike = float(spread.metadata["short_strike"])
        long_strike = float(spread.metadata["long_strike"])

    expiration = str(spread.metadata["expiration"])
    underlying = str(spread.metadata["underlying"])
    option_type = "put" if signal.strategy == SpreadStrategy.PUT_CREDIT_SPREAD else "call"
    available = await fetch_alpaca_option_strikes(settings, underlying, expiration, option_type)
    if not available:
        logger.warning("No Alpaca strikes returned for %s %s — using computed strikes", underlying, expiration)
        return spread

    original = (short_strike, long_strike)
    if option_type == "put":
        short_strike, long_strike = snap_put_credit_strikes(short_strike, long_strike, available)
    else:
        short_strike, long_strike = snap_call_credit_strikes(short_strike, long_strike, available)

    if (short_strike, long_strike) != original:
        logger.info(
            "Snapped %s strikes for Alpaca: short %.2f→%.2f long %.2f→%.2f",
            underlying,
            original[0],
            short_strike,
            original[1],
            long_strike,
        )

    short_sym = format_occ_symbol(underlying, expiration, option_type, short_strike)
    long_sym = format_occ_symbol(underlying, expiration, option_type, long_strike)
    return SpreadPackage(
        qty=spread.qty,
        legs=[
            SpreadLeg(symbol=short_sym, side="sell", position_intent="sell_to_open"),
            SpreadLeg(symbol=long_sym, side="buy", position_intent="buy_to_open"),
        ],
        metadata={
            **spread.metadata,
            "short_strike": short_strike,
            "long_strike": long_strike,
            "strikes_snapped": (short_strike, long_strike) != original,
        },
    )


def build_alpaca_mleg_payload(
    spread: SpreadPackage,
    *,
    limit_price: float,
    time_in_force: str = "day",
    closing: bool = False,
) -> dict:
    """Alpaca paper/live multi-leg options order (order_class=mleg)."""
    if closing:
        legs = [
            {
                "symbol": spread.legs[0].symbol,
                "ratio_qty": "1",
                "side": "buy",
                "position_intent": "buy_to_close",
            },
            {
                "symbol": spread.legs[1].symbol,
                "ratio_qty": "1",
                "side": "sell",
                "position_intent": "sell_to_close",
            },
        ]
        signed_limit = format_alpaca_limit_price(limit_price, is_credit=False)
    else:
        legs = [
            {
                "symbol": spread.legs[0].symbol,
                "ratio_qty": "1",
                "side": "sell",
                "position_intent": "sell_to_open",
            },
            {
                "symbol": spread.legs[1].symbol,
                "ratio_qty": "1",
                "side": "buy",
                "position_intent": "buy_to_open",
            },
        ]
        signed_limit = format_alpaca_limit_price(limit_price, is_credit=True)

    return {
        "order_class": "mleg",
        "qty": spread.qty,
        "type": "limit",
        "limit_price": signed_limit,
        "time_in_force": time_in_force,
        "legs": legs,
    }


def build_alpaca_entry_payload(spread: SpreadPackage) -> dict:
    credit = float(spread.metadata["limit_credit"])
    return build_alpaca_mleg_payload(spread, limit_price=credit, time_in_force="day", closing=False)


def build_alpaca_close_payload(spread: SpreadPackage, close_debit: float) -> dict:
    return build_alpaca_mleg_payload(spread, limit_price=close_debit, time_in_force="gtc", closing=True)


def build_close_spread_payload(spread: SpreadPackage, close_debit: float) -> dict:
    qty = int(spread.qty)
    return {
        "time-in-force": "GTC",
        "order-type": "Limit",
        "price": f"{close_debit:.2f}",
        "price-effect": "Debit",
        "legs": [
            {
                "instrument-type": "Equity Option",
                "symbol": to_tastytrade_symbol(spread.legs[0].symbol),
                "quantity": qty,
                "action": "Buy to Close",
            },
            {
                "instrument-type": "Equity Option",
                "symbol": to_tastytrade_symbol(spread.legs[1].symbol),
                "quantity": qty,
                "action": "Sell to Close",
            },
        ],
    }


def build_take_profit_payload(spread: SpreadPackage, take_profit_pct: float, settings: Settings) -> dict:
    credit = float(spread.metadata["limit_credit"])
    close_debit = round(credit * take_profit_pct, 2)
    if settings.use_alpaca:
        payload = build_alpaca_close_payload(spread, close_debit)
    else:
        payload = build_close_spread_payload(spread, close_debit)
    payload["_meta"] = {
        "entry_credit": credit,
        "close_debit": close_debit,
        "profit_locked": round(credit - close_debit, 2),
    }
    return payload


def build_stop_loss_payload(spread: SpreadPackage, stop_loss_multiplier: float, settings: Settings) -> dict:
    """
    GTC stop-loss safety net: buy back spread at N× entry credit (default 2×).

    Example: $0.50 credit entry → stop at $1.00 debit to close.
    """
    credit = float(spread.metadata["limit_credit"])
    close_debit = round(credit * stop_loss_multiplier, 2)
    if settings.use_alpaca:
        payload = build_alpaca_close_payload(spread, close_debit)
    else:
        payload = build_close_spread_payload(spread, close_debit)
    payload["_meta"] = {
        "entry_credit": credit,
        "stop_loss_multiplier": stop_loss_multiplier,
        "close_debit": close_debit,
        "max_loss_estimate": round(close_debit - credit, 2),
    }
    return payload


def check_danger(underlying: float, short_strike: float, danger_pct: float, ticker: str) -> tuple[bool, str, float]:
    distance_pct = abs(underlying - short_strike) / short_strike
    if distance_pct <= danger_pct:
        msg = (
            f"CRITICAL: {ticker} ${underlying:.2f} is {distance_pct * 100:.2f}% "
            f"from short strike ${short_strike:.2f} (limit {danger_pct * 100:.1f}%)"
        )
        logger.critical(msg)
        return True, msg, distance_pct
    return False, "", distance_pct


def parse_occ_symbol(symbol: str) -> dict[str, Any] | None:
    """Parse compact OCC e.g. SPY260605P00585000."""
    sym = symbol.strip().upper()
    idx = next((i for i, ch in enumerate(sym) if ch.isdigit()), None)
    if idx is None or idx < 1:
        return None
    underlying = sym[:idx]
    rest = sym[idx:]
    if len(rest) < 7:
        return None
    exp = rest[:6]
    opt = rest[6]
    strike_raw = rest[7:]
    try:
        exp_date = datetime.strptime(exp, "%y%m%d").strftime("%Y-%m-%d")
        strike = int(strike_raw) / 1000.0
    except ValueError:
        return None
    return {
        "underlying": underlying,
        "expiration": exp_date,
        "option_type": "call" if opt == "C" else "put",
        "strike": strike,
        "symbol": sym,
    }


def estimate_survival_odds_put_credit(
    underlying: float,
    short_strike: float,
    long_strike: float | None,
    *,
    danger_pct: float,
) -> tuple[float, list[str]]:
    """
    Heuristic probability SPY put credit spread expires OTM (keep premium).
    Uses distance to short strike; flags pin/gamma when inside danger band.
    """
    notes: list[str] = []
    if short_strike <= 0:
        return 0.5, notes

    pct_above_short = (underlying - short_strike) / short_strike
    if underlying <= short_strike:
        survival = max(0.05, 0.25 + pct_above_short * 2)
        notes.append("Price at/below short put — assignment/exercise risk elevated")
    elif pct_above_short <= danger_pct:
        survival = min(0.75, 0.35 + (pct_above_short / danger_pct) * 0.4)
        notes.append("Inside danger band — 0DTE/dealer pin may accelerate moves")
    else:
        survival = min(0.95, 0.72 + pct_above_short * 2)

    if long_strike is not None and underlying <= long_strike:
        survival = min(survival, 0.15)
        notes.append("Below long strike — max-loss zone for put credit spread")

    notes.append(
        "Crowded strikes: other traders' stops may amplify moves near short strike (safety-net exercise)"
    )
    return round(max(0.0, min(1.0, survival)), 4), notes


def spread_from_put_credit_position(
    short_pos: dict[str, Any],
    long_pos: dict[str, Any],
    *,
    qty: int,
    credit: float,
) -> SpreadPackage:
    short_meta = parse_occ_symbol(str(short_pos.get("symbol", ""))) or {}
    long_meta = parse_occ_symbol(str(long_pos.get("symbol", ""))) or {}
    return SpreadPackage(
        qty=str(qty),
        legs=[
            SpreadLeg(
                symbol=str(short_pos["symbol"]),
                side="sell",
                position_intent="sell_to_open",
            ),
            SpreadLeg(
                symbol=str(long_pos["symbol"]),
                side="buy",
                position_intent="buy_to_open",
            ),
        ],
        metadata={
            "underlying": short_meta.get("underlying", "SPY"),
            "strategy": "put_credit_spread",
            "expiration": short_meta.get("expiration"),
            "short_strike": short_meta.get("strike"),
            "long_strike": long_meta.get("strike"),
            "limit_credit": credit,
        },
    )


def find_put_credit_spreads_in_positions(
    positions: list[dict[str, Any]],
    ticker: str,
    *,
    short_strike: float | None = None,
    long_strike: float | None = None,
    strike_tolerance: float = 0.51,
) -> list[SpreadPackage]:
    """Match open put credit spreads from Alpaca option positions."""
    ticker = ticker.upper()
    puts: list[dict[str, Any]] = []
    for p in positions:
        sym = str(p.get("symbol", ""))
        meta = parse_occ_symbol(sym)
        if not meta or meta["underlying"] != ticker or meta["option_type"] != "put":
            continue
        try:
            qty = int(float(p.get("qty", 0)))
        except (TypeError, ValueError):
            continue
        if qty == 0:
            continue
        puts.append({**p, "_meta": meta, "_qty": qty})

    shorts = [p for p in puts if p["_qty"] < 0]
    longs = [p for p in puts if p["_qty"] > 0]
    spreads: list[SpreadPackage] = []

    for sp in shorts:
        sm = sp["_meta"]
        for lp in longs:
            lm = lp["_meta"]
            if sm["expiration"] != lm["expiration"]:
                continue
            if sm["strike"] <= lm["strike"]:
                continue
            if short_strike is not None and abs(sm["strike"] - short_strike) > strike_tolerance:
                continue
            if long_strike is not None and abs(lm["strike"] - long_strike) > strike_tolerance:
                continue
            qty = min(abs(sp["_qty"]), lp["_qty"])
            credit = float(sp.get("avg_entry_price") or 0) + float(lp.get("avg_entry_price") or 0)
            if credit <= 0:
                credit = 0.35
            spreads.append(spread_from_put_credit_position(sp, lp, qty=qty, credit=abs(credit)))
    return spreads


async def fetch_alpaca_positions(settings: Settings) -> list[dict[str, Any]]:
    base = settings.apca_api_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{base}/v2/positions", headers=_alpaca_headers(settings))
    if not r.is_success:
        logger.warning("Alpaca positions fetch failed: %s", r.text[:200])
        return []
    data = r.json()
    return data if isinstance(data, list) else []


async def cancel_alpaca_open_orders_for_symbols(settings: Settings, symbols: set[str]) -> int:
    """Cancel resting TP/SL before emergency warning close."""
    base = settings.apca_api_base_url.rstrip("/")
    canceled = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{base}/v2/orders?status=open&limit=100", headers=_alpaca_headers(settings))
        if not r.is_success:
            return 0
        body = r.json()
        orders = body if isinstance(body, list) else []
        for order in orders:
            legs = order.get("legs") or []
            leg_syms = {str(leg.get("symbol", "")) for leg in legs}
            if order.get("symbol") in symbols:
                leg_syms.add(str(order["symbol"]))
            if not leg_syms.intersection(symbols):
                continue
            oid = order.get("id")
            if not oid:
                continue
            cr = await client.delete(f"{base}/v2/orders/{oid}", headers=_alpaca_headers(settings))
            if cr.is_success:
                canceled += 1
    return canceled


def resolve_warning_close_debit(
    spread: SpreadPackage,
    settings: Settings,
    *,
    override_debit: float | None = None,
) -> float:
    if override_debit is not None and override_debit > 0:
        return round(override_debit, 2)
    credit = float(spread.metadata.get("limit_credit", settings.default_limit_credit))
    return round(credit * settings.warning_close_multiplier, 2)


# ── Notifications ─────────────────────────────────────────────────────────────


async def notify(settings: Settings, title: str, body: str, level: str = "INFO") -> dict:
    message = f"**[{level}] {title}**\n{body}"
    results: dict = {}

    if not settings.discord_webhook_url and not settings.telegram_configured:
        logger.info("Notify: %s", message)
        return results

    async with httpx.AsyncClient(timeout=15.0) as client:
        if settings.discord_webhook_url:
            r = await client.post(settings.discord_webhook_url, json={"content": message[:2000]})
            results["discord"] = r.status_code
        if settings.telegram_configured:
            url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
            r = await client.post(url, json={"chat_id": settings.telegram_chat_id, "text": message[:4000]})
            results["telegram"] = r.status_code
    return results


# ── Broker adapters ───────────────────────────────────────────────────────────


async def tastytrade_login(client: httpx.AsyncClient, settings: Settings) -> str:
    r = await client.post(
        "/sessions",
        json={"login": settings.username, "password": settings.password, "remember-me": True},
    )
    r.raise_for_status()
    token = r.json().get("data", {}).get("session-token")
    if not token:
        raise ValueError("No session-token from Tastytrade cert login")
    return token


async def submit_tastytrade_order(settings: Settings, payload: dict, *, dry_run: bool) -> OrderResult:
    clean = {k: v for k, v in payload.items() if not k.startswith("_")}
    account = settings.tastytrade_account_number
    path = f"/accounts/{account}/orders/dry-run" if dry_run else f"/accounts/{account}/orders"

    if not settings.configured:
        return OrderResult(
            success=True if dry_run else False,
            message="Packaged only — add Tastytrade cert credentials to submit",
            dry_run=True,
            payload=clean,
        )

    base = settings.tastytrade_api_base_url.rstrip("/")
    async with httpx.AsyncClient(base_url=base, timeout=30.0) as client:
        token = await tastytrade_login(client, settings)
        r = await client.post(
            path,
            headers={"Authorization": token, "Content-Type": "application/json"},
            json=clean,
        )
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text}

    if r.is_success:
        return OrderResult(success=True, message=f"Accepted at {path}", dry_run=dry_run, payload=clean, broker_response=body)

    return OrderResult(success=False, message=f"Rejected ({r.status_code})", dry_run=dry_run, payload=clean, broker_response=body)


async def submit_alpaca_order(settings: Settings, payload: dict, *, dry_run: bool) -> OrderResult:
    clean = {k: v for k, v in payload.items() if not k.startswith("_")}

    if dry_run:
        return OrderResult(
            success=True,
            message="Sandbox mode — Alpaca order packaged but not sent",
            dry_run=True,
            payload=clean,
        )

    if not settings.alpaca_configured:
        return OrderResult(
            success=False,
            message="Alpaca credentials missing — set APCA_API_KEY_ID and APCA_API_SECRET_KEY",
            dry_run=False,
            payload=clean,
        )

    base = settings.apca_api_base_url.rstrip("/")
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "Apca-Api-Key-Id": settings.alpaca_key,
        "Apca-Api-Secret-Key": settings.alpaca_secret,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{base}/v2/orders", headers=headers, json=clean)

    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}

    if r.is_success:
        return OrderResult(
            success=True,
            message="Alpaca paper order accepted",
            dry_run=False,
            payload=clean,
            broker_response=body,
        )

    return OrderResult(
        success=False,
        message=f"Alpaca rejected ({r.status_code})",
        dry_run=False,
        payload=clean,
        broker_response=body,
    )


async def submit_order(settings: Settings, payload: dict, *, dry_run: bool) -> OrderResult:
    if settings.use_alpaca:
        return await submit_alpaca_order(settings, payload, dry_run=dry_run)
    return await submit_tastytrade_order(settings, payload, dry_run=dry_run)


def _alpaca_headers(settings: Settings) -> dict[str, str]:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "Apca-Api-Key-Id": settings.alpaca_key,
        "Apca-Api-Secret-Key": settings.alpaca_secret,
    }


async def fetch_alpaca_order(settings: Settings, order_id: str) -> dict[str, Any]:
    base = settings.apca_api_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{base}/v2/orders/{order_id}", headers=_alpaca_headers(settings))
    if not r.is_success:
        logger.warning("Alpaca order lookup %s failed (%s): %s", order_id, r.status_code, r.text[:200])
        return {}
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}


async def replace_alpaca_order_limit(settings: Settings, order_id: str, credit: float) -> bool:
    """Lower limit credit on a resting mleg entry to improve fill odds."""
    base = settings.apca_api_base_url.rstrip("/")
    payload = {"limit_price": format_alpaca_limit_price(credit, is_credit=True)}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.patch(f"{base}/v2/orders/{order_id}", headers=_alpaca_headers(settings), json=payload)
    if r.is_success:
        logger.info("Chase fill: order %s repriced to $%s credit", order_id, credit)
        return True
    logger.warning("Chase fill replace failed (%s): %s", r.status_code, r.text[:200])
    return False


async def wait_and_chase_alpaca_entry_fill(
    settings: Settings,
    order_id: str,
    initial_credit: float,
) -> dict[str, Any] | None:
    """
    Poll for fill; if still open, repeatedly lower limit credit until filled or floor hit.
    Needed because Alpaca paper often leaves mleg orders at status=new when limit is too high.
    """
    terminal = {"canceled", "expired", "rejected", "failed", "done_for_day"}
    credit = round(initial_credit, 2)
    floor = round(settings.entry_min_credit, 2)
    max_attempts = settings.entry_chase_max_attempts if settings.auto_chase_entry_fill else 0

    step = 0.01 if floor <= 0.01 else 0.05

    async def _poll_window() -> dict[str, Any] | None:
        deadline = time.monotonic() + settings.entry_chase_wait_seconds
        while time.monotonic() < deadline:
            order = await fetch_alpaca_order(settings, order_id)
            status = str(order.get("status", "")).lower()
            if status == "filled":
                logger.info("Alpaca entry %s filled (credit=$%s)", order_id, credit)
                return order
            if status in terminal:
                logger.warning("Alpaca entry %s stopped as %s", order_id, status)
                return None
            await asyncio.sleep(settings.entry_chase_poll_seconds)
        return {}

    for attempt in range(max_attempts + 1):
        order = await _poll_window()
        if order is None:
            return None
        if order.get("status", "").lower() == "filled" or order.get("filled_avg_price"):
            logger.info("Alpaca entry %s filled (attempt %s, credit=$%s)", order_id, attempt, credit)
            return order

        if attempt >= max_attempts:
            break

        new_credit = max(round(credit * 0.75 - step, 2), floor)
        if new_credit >= credit:
            new_credit = max(round(credit - step, 2), floor)
        if new_credit >= credit:
            logger.info("Chase fill: order %s at floor $%s — extra polls", order_id, credit)
            for extra in range(settings.entry_chase_floor_extra_polls):
                order = await _poll_window()
                if order is None:
                    return None
                if order.get("status", "").lower() == "filled" or order.get("filled_avg_price"):
                    logger.info(
                        "Alpaca entry %s filled at floor (extra poll %s, credit=$%s)",
                        order_id,
                        extra + 1,
                        credit,
                    )
                    return order
            break

        if not await replace_alpaca_order_limit(settings, order_id, new_credit):
            break
        credit = new_credit
        await notify(
            settings,
            "Chasing Entry Fill",
            f"Order {order_id[:8]}… repriced to ${credit:.2f} credit (attempt {attempt + 1})",
            "INFO",
        )

    logger.warning("Alpaca entry %s not filled after chase — last credit $%s", order_id, credit)
    return None


async def wait_for_alpaca_entry_fill(
    settings: Settings,
    order_id: str,
    *,
    max_wait_sec: int,
    poll_sec: float,
) -> dict[str, Any] | None:
    """
    Poll until entry fills — only for placing GTC exits, not ongoing position tracking.
    Matches master plan: brief wait, then resting orders on broker.
    """
    terminal = {"canceled", "expired", "rejected", "failed", "done_for_day"}
    deadline = time.monotonic() + max_wait_sec
    while time.monotonic() < deadline:
        order = await fetch_alpaca_order(settings, order_id)
        status = str(order.get("status", "")).lower()
        if status == "filled":
            logger.info("Alpaca entry %s filled — placing GTC exits", order_id)
            return order
        if status in terminal:
            logger.warning("Alpaca entry %s stopped as %s — auto exits not placed", order_id, status)
            return None
        await asyncio.sleep(poll_sec)
    logger.warning("Alpaca entry %s not filled within %ss — auto exits not placed", order_id, max_wait_sec)
    return None


def resolve_entry_credit(spread: SpreadPackage, filled_order: dict[str, Any] | None) -> float:
    credit = float(spread.metadata["limit_credit"])
    if not filled_order:
        return credit
    avg = filled_order.get("filled_avg_price")
    if avg is not None:
        try:
            val = abs(float(avg))
            if val > 0:
                return round(val, 2)
        except (TypeError, ValueError):
            pass
    return credit


def spread_with_credit(spread: SpreadPackage, credit: float) -> SpreadPackage:
    return SpreadPackage(
        qty=spread.qty,
        legs=spread.legs,
        metadata={**spread.metadata, "limit_credit": credit},
    )


async def alpaca_place_exits_after_fill(
    settings: Settings,
    spread: SpreadPackage,
    entry_order_id: str,
    *,
    initial_credit: float | None = None,
) -> None:
    """Background task: chase entry fill, then submit GTC take-profit + stop-loss."""
    if not settings.is_live or not settings.alpaca_configured:
        return

    start_credit = initial_credit if initial_credit is not None else float(spread.metadata["limit_credit"])
    if settings.auto_chase_entry_fill:
        filled = await wait_and_chase_alpaca_entry_fill(settings, entry_order_id, start_credit)
    else:
        filled = await wait_for_alpaca_entry_fill(
            settings,
            entry_order_id,
            max_wait_sec=settings.alpaca_exit_fill_timeout,
            poll_sec=settings.alpaca_exit_poll_seconds,
        )
    if not filled:
        await notify(
            settings,
            "Entry Not Filled",
            f"Order {entry_order_id} never filled (chased down to ${settings.entry_min_credit}) — no GTC TP/SL",
            "CRITICAL",
        )
        return

    spread = spread_with_credit(spread, resolve_entry_credit(spread, filled))
    ticker = str(spread.metadata.get("underlying", "SPY"))

    if settings.auto_take_profit:
        tp_payload = build_take_profit_payload(spread, settings.take_profit_pct, settings)
        tp = await submit_alpaca_order(settings, tp_payload, dry_run=False)
        if tp.success:
            meta = tp_payload.get("_meta", {})
            await notify(
                settings,
                "GTC Take-Profit Resting",
                f"{ticker} close at ${meta.get('close_debit')} debit — locks ~${meta.get('profit_locked')}",
                "SUCCESS",
            )
        else:
            logger.error("Alpaca take-profit rejected: %s", tp.broker_response)

    if settings.auto_stop_loss:
        sl_payload = build_stop_loss_payload(spread, settings.stop_loss_multiplier, settings)
        sl = await submit_alpaca_order(settings, sl_payload, dry_run=False)
        if sl.success:
            meta = sl_payload.get("_meta", {})
            await notify(
                settings,
                "GTC Stop-Loss Resting",
                f"{ticker} close at ${meta.get('close_debit')} debit — caps loss ~${meta.get('max_loss_estimate')}",
                "SUCCESS",
            )
        else:
            logger.error("Alpaca stop-loss rejected: %s", sl.broker_response)


def webhook_auth_error(provided: str | None, expected: str) -> str | None:
    """Return a user-facing error, or None when auth passes."""
    if not expected:
        return None
    if provided == expected:
        return None
    if provided is None:
        return (
            "Missing webhookSecret in TradingView alert message — paste full JSON from "
            "templates/tradingview-entry-autofill.json (includes webhookSecret field)"
        )
    return "Invalid webhook secret — must match Render env WEBHOOK_SECRET exactly"


async def submit_entry_from_signal(
    settings: Settings,
    signal: TradingViewSignal,
) -> tuple[SpreadPackage, OrderResult, float | None]:
    """
    Build spread, resolve credit, submit entry. Returns (spread, result, entry_credit).
    entry_credit is limit credit used for Alpaca chase scheduling.
    """
    dry_run = not settings.is_live
    spread = build_spread(signal, settings)
    if settings.use_alpaca:
        spread = await align_spread_to_alpaca(settings, spread, signal)
        spread = await resolve_entry_limit_credit(settings, spread, signal)

    if signal.signal_price is not None:
        danger, risk_msg, _ = check_danger(
            signal.signal_price,
            float(spread.metadata["short_strike"]),
            settings.danger_zone_pct,
            signal.ticker,
        )
        if danger and risk_msg:
            await notify(settings, "Entry Near Danger Zone", risk_msg, "CRITICAL")

    entry_payload = build_alpaca_entry_payload(spread) if settings.use_alpaca else build_entry_payload(spread)
    logger.info("Submitting %s entry order", "Alpaca mleg" if settings.use_alpaca else "Tastytrade")
    entry = await submit_order(settings, entry_payload, dry_run=dry_run)
    entry_credit = float(spread.metadata["limit_credit"]) if settings.use_alpaca else None
    return spread, entry, entry_credit


def schedule_alpaca_exits_after_entry(
    settings: Settings,
    spread: SpreadPackage,
    entry: OrderResult,
    entry_credit: float | None,
) -> str | None:
    """Queue GTC TP/SL after Alpaca entry fill chase. Returns order id if scheduled."""
    if not settings.use_alpaca or not (settings.auto_take_profit or settings.auto_stop_loss):
        return None
    order_id = (entry.broker_response or {}).get("id")
    if not order_id:
        logger.error("Alpaca entry accepted but no order id — cannot schedule auto exits")
        return None
    credit = entry_credit if entry_credit is not None else float(spread.metadata["limit_credit"])
    asyncio.create_task(
        alpaca_place_exits_after_fill(
            settings,
            spread,
            str(order_id),
            initial_credit=credit,
        )
    )
    logger.info("Alpaca: scheduled auto GTC exits after fill for order %s", order_id)
    return str(order_id)


async def process_entry_alert(settings: Settings, signal: TradingViewSignal) -> None:
    """Run Alpaca/Tastytrade entry + exit scheduling (TradingView gets an instant 200 first)."""
    dry_run = not settings.is_live
    try:
        spread, entry, entry_credit = await submit_entry_from_signal(settings, signal)
        expiration = spread.metadata["expiration"]

        if entry.success:
            await notify(
                settings,
                "Entry Submitted",
                f"{signal.ticker} {spread.metadata['strategy']} exp={expiration} credit=${spread.metadata['limit_credit']}",
                "SUCCESS" if settings.is_live else "INFO",
            )
        else:
            await notify(settings, "Entry Rejected", entry.message, "CRITICAL")
            return

        if not settings.use_alpaca:
            if settings.auto_take_profit:
                tp_payload = build_take_profit_payload(spread, settings.take_profit_pct, settings)
                take_profit = await submit_order(settings, tp_payload, dry_run=dry_run)
                if take_profit.success:
                    meta = tp_payload.get("_meta", {})
                    await notify(
                        settings,
                        "GTC Take-Profit Resting",
                        f"{signal.ticker} close at ${meta.get('close_debit')} debit — locks ~${meta.get('profit_locked')}",
                        "SUCCESS",
                    )

            if settings.auto_stop_loss:
                sl_payload = build_stop_loss_payload(spread, settings.stop_loss_multiplier, settings)
                stop_loss = await submit_order(settings, sl_payload, dry_run=dry_run)
                if stop_loss.success:
                    meta = sl_payload.get("_meta", {})
                    await notify(
                        settings,
                        "GTC Stop-Loss Resting",
                        f"{signal.ticker} close at ${meta.get('close_debit')} debit — caps loss ~${meta.get('max_loss_estimate')}",
                        "SUCCESS",
                    )

        else:
            schedule_alpaca_exits_after_entry(settings, spread, entry, entry_credit)
    except Exception as exc:
        logger.exception("Background entry processing failed: %s", exc)
        await notify(settings, "Entry Failed", str(exc), "CRITICAL")


def coerce_signal(payload: dict, settings: Settings) -> TradingViewSignal:
    merged = {
        "strikeOffsetShort": settings.default_strike_offset_short,
        "strikeOffsetLong": settings.default_strike_offset_long,
        "limitCredit": settings.default_limit_credit,
        "dteFilter": settings.default_dte_filter,
        **payload,
    }
    merged.setdefault("ticker", settings.default_underlying)
    merged.setdefault("quantity", settings.default_quantity)
    signal = TradingViewSignal.model_validate(merged)
    if settings.max_quantity > 0 and signal.quantity > settings.max_quantity:
        logger.warning("Quantity %s capped to MAX_QUANTITY=%s", signal.quantity, settings.max_quantity)
        signal.quantity = settings.max_quantity
    return signal


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="spy-options-bridge",
    version="5.5.5",
    description="TradingView → Alpaca Paper multi-leg SPY credit spreads + auto GTC exits",
)


@app.on_event("startup")
async def startup_log() -> None:
    s = get_settings()
    broker_label = "Alpaca Paper" if s.use_alpaca else "Tastytrade Cert"
    logger.info(
        "spy-options-bridge v5.5.5 started | broker=%s (%s) | configured=%s | mode=%s | auto_tp=%s auto_sl=%s chase=%s paper_force=%s",
        s.broker,
        broker_label,
        s.configured,
        s.execution_mode,
        s.auto_take_profit,
        s.auto_stop_loss,
        s.auto_chase_entry_fill,
        s.paper_force_min_fill and s.is_alpaca_paper,
    )

SECRET_BODY_KEYS = ("webhookSecret", "webhook_secret", "secret")


def extract_webhook_secret(payload: dict[str, Any], header: str | None) -> tuple[str | None, dict[str, Any]]:
    """Accept secret via header or JSON body (TradingView cannot send custom headers)."""
    clean = dict(payload)
    body_secret: str | None = None
    for key in SECRET_BODY_KEYS:
        if key in clean and clean[key] is not None:
            body_secret = str(clean.pop(key))
            break
    return header or body_secret, clean


@app.get("/health")
async def health() -> dict[str, str]:
    s = get_settings()
    broker_name = "alpaca" if s.use_alpaca else s.broker
    return {
        "status": "ok",
        "version": "5.5.5",
        "auto_take_profit": str(s.auto_take_profit),
        "auto_stop_loss": str(s.auto_stop_loss),
        "broker": broker_name,
        "broker_label": "Alpaca Paper" if s.use_alpaca else "Tastytrade Cert",
        "mode": s.execution_mode,
        "configured": str(s.configured),
        "api": s.apca_api_base_url if s.use_alpaca else s.tastytrade_api_base_url,
        "dte_filter_default": s.default_dte_filter,
        "auto_close_on_warning": str(s.auto_close_on_warning),
        "warning_close_multiplier": str(s.warning_close_multiplier),
        "default_fill_mode": s.default_fill_mode,
        "auto_chase_entry_fill": str(s.auto_chase_entry_fill),
        "entry_chase_wait_seconds": str(s.entry_chase_wait_seconds),
        "entry_chase_poll_seconds": str(s.entry_chase_poll_seconds),
        "entry_min_credit": str(s.entry_min_credit),
        "paper_force_min_fill": str(s.paper_force_min_fill and s.is_alpaca_paper),
        "deploy_file": "main.py (root — NOT app/main.py)",
    }


@app.get("/ping")
async def ping() -> dict[str, str]:
    """Lightweight keep-alive for cron pings (prevents Render free-tier cold starts)."""
    return {"status": "ok", "version": "5.5.5"}


@app.post("/exercise/entry")
async def exercise_entry_endpoint(
    request: Request,
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
) -> JSONResponse:
    """
    Manual burst-test entry (no TradingView): forces fillMode=exercise, submits synchronously,
    returns Alpaca order id for polling. GTC TP/SL chase runs in background.
    """
    settings = get_settings()
    dry_run = not settings.is_live

    try:
        payload = await request.json()
    except Exception as exc:
        return JSONResponse(
            status_code=200,
            content={"success": False, "message": f"Invalid JSON: {exc}", "dry_run": dry_run},
        )

    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=200,
            content={"success": False, "message": "Body must be a JSON object", "dry_run": dry_run},
        )

    provided, payload = extract_webhook_secret(payload, x_webhook_secret)
    auth_err = webhook_auth_error(provided, settings.webhook_secret)
    if auth_err:
        return JSONResponse(status_code=200, content={"success": False, "message": auth_err, "dry_run": dry_run})

    payload = dict(payload)
    payload["fillMode"] = "exercise"
    payload.setdefault("action", "PUT_CREDIT_SPREAD")

    try:
        signal = coerce_signal(payload, settings)
        build_spread(signal, settings)
    except (ValidationError, ValueError) as exc:
        return JSONResponse(
            status_code=200,
            content={"success": False, "message": f"Bad payload: {exc}", "dry_run": dry_run},
        )

    try:
        spread, entry, entry_credit = await submit_entry_from_signal(settings, signal)
    except Exception as exc:
        logger.exception("Exercise entry failed: %s", exc)
        return JSONResponse(
            status_code=200,
            content={"success": False, "message": str(exc), "dry_run": dry_run},
        )

    order_id = schedule_alpaca_exits_after_entry(settings, spread, entry, entry_credit)
    return JSONResponse(
        status_code=200,
        content={
            "success": entry.success,
            "message": entry.message,
            "dry_run": dry_run,
            "order_id": order_id,
            "limit_credit": spread.metadata.get("limit_credit"),
            "expiration": spread.metadata.get("expiration"),
            "broker_response": entry.broker_response if entry.success else None,
        },
    )


@app.post("/entry", response_model=EntryResponse)
@app.post("/webhook", response_model=EntryResponse)
async def entry_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
) -> EntryResponse:
    """
    ENTRY endpoint — submit multi-leg credit spread + GTC take-profit + GTC stop-loss.
    TradingView should point here (or /webhook alias).
    """
    settings = get_settings()
    dry_run = not settings.is_live

    try:
        payload = await request.json()
    except Exception as exc:
        logger.warning("Invalid webhook JSON: %s", exc)
        return JSONResponse(  # type: ignore[return-value]
            status_code=200,
            content={
                "success": False,
                "message": f"Invalid JSON body — use JSON alert message only: {exc}",
                "dry_run": dry_run,
                "notifications": {},
            },
        )

    if not isinstance(payload, dict):
        return JSONResponse(  # type: ignore[return-value]
            status_code=200,
            content={
                "success": False,
                "message": "Alert body must be a JSON object — paste templates/tradingview-entry-autofill.json",
                "dry_run": dry_run,
                "notifications": {},
            },
        )

    provided, payload = extract_webhook_secret(payload, x_webhook_secret)
    auth_err = webhook_auth_error(provided, settings.webhook_secret)
    if auth_err:
        logger.warning("Webhook auth failed: %s", auth_err)
        return JSONResponse(  # type: ignore[return-value]
            status_code=200,
            content={
                "success": False,
                "message": auth_err,
                "dry_run": dry_run,
                "notifications": {},
            },
        )

    try:
        signal = coerce_signal(payload, settings)
        build_spread(signal, settings)
    except (ValidationError, ValueError) as exc:
        logger.warning("Signal rejected: %s", exc)
        return JSONResponse(  # type: ignore[return-value]
            status_code=200,
            content={
                "success": False,
                "message": f"Bad alert payload — check TradingView JSON: {exc}",
                "dry_run": dry_run,
                "notifications": {},
            },
        )

    background_tasks.add_task(process_entry_alert, settings, signal)
    logger.info("Queued background entry for %s qty=%s", signal.ticker, signal.quantity)
    return JSONResponse(  # type: ignore[return-value]
        status_code=200,
        content={
            "success": True,
            "message": f"Alert accepted — processing {signal.ticker} order in background",
            "dry_run": dry_run,
            "processing": True,
            "notifications": {},
        },
    )


@app.post("/warning", response_model=WarningResponse)
async def warning_endpoint(
    request: Request,
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
) -> WarningResponse:
    """
    WARNING endpoint — danger zone protocol.

    - Always evaluates survival odds + risk message.
    - Default: AUTO_CLOSE_ON_WARNING=true submits multi-leg buy-to-close on Alpaca.
    - Set overrideAutoClose=true in JSON for notify-only (no close).
    - Set forceAutoClose=true to close even if AUTO_CLOSE_ON_WARNING=false.
    """
    settings = get_settings()
    dry_run = not settings.is_live

    try:
        payload = await request.json()
    except Exception as exc:
        logger.warning("Invalid warning webhook JSON: %s", exc)
        return JSONResponse(  # type: ignore[return-value]
            status_code=200,
            content={
                "danger_zone": False,
                "action_taken": "invalid_json",
                "risk_warning": f"Invalid JSON body: {exc}",
                "notifications": {},
            },
        )

    if not isinstance(payload, dict):
        return JSONResponse(  # type: ignore[return-value]
            status_code=200,
            content={
                "danger_zone": False,
                "action_taken": "invalid_payload",
                "risk_warning": "Alert body must be a JSON object",
                "notifications": {},
            },
        )

    provided, payload = extract_webhook_secret(payload, x_webhook_secret)
    auth_err = webhook_auth_error(provided, settings.webhook_secret)
    if auth_err:
        logger.warning("Warning webhook auth failed: %s", auth_err)
        return JSONResponse(  # type: ignore[return-value]
            status_code=200,
            content={
                "danger_zone": False,
                "action_taken": "auth_failed",
                "risk_warning": auth_err,
                "notifications": {},
            },
        )

    warning = WarningSignal.model_validate(payload)

    danger, msg, distance = check_danger(
        warning.signal_price,
        warning.short_strike,
        settings.danger_zone_pct,
        warning.ticker,
    )

    survival, protocol_notes = estimate_survival_odds_put_credit(
        warning.signal_price,
        warning.short_strike,
        warning.long_strike,
        danger_pct=settings.danger_zone_pct,
    )

    notifications: dict = {}
    action_taken = "no_danger"
    close_order: OrderResult | None = None
    positions_matched = 0

    if danger and msg:
        notify_body = (
            f"{msg}\nSurvival odds (expire OTM / keep premium): **{survival * 100:.1f}%**\n"
            + "\n".join(f"- {n}" for n in protocol_notes)
        )
        notifications["warning"] = await notify(
            settings,
            "DANGER — Warning Protocol",
            notify_body,
            "CRITICAL",
        )

    if not danger:
        return WarningResponse(
            danger_zone=False,
            risk_warning=None,
            distance_pct=round(distance * 100, 3),
            action_taken=action_taken,
            survival_odds_expire_otm=survival,
            protocol_notes=protocol_notes,
            notifications=notifications,
        )

    if warning.override_auto_close:
        action_taken = "notify_only_override"
        await notify(
            settings,
            "Warning — Override Active",
            f"{msg}\nAuto-close SKIPPED (overrideAutoClose=true). Survival odds: {survival * 100:.1f}%",
            "WARNING",
        )
        return WarningResponse(
            danger_zone=True,
            risk_warning=msg,
            distance_pct=round(distance * 100, 3),
            action_taken=action_taken,
            survival_odds_expire_otm=survival,
            protocol_notes=protocol_notes,
            notifications=notifications,
        )

    should_close = warning.force_auto_close or settings.auto_close_on_warning
    if not should_close:
        action_taken = "notify_only_disabled"
        return WarningResponse(
            danger_zone=True,
            risk_warning=msg,
            distance_pct=round(distance * 100, 3),
            action_taken=action_taken,
            survival_odds_expire_otm=survival,
            protocol_notes=protocol_notes,
            notifications=notifications,
        )

    if not settings.use_alpaca or not settings.alpaca_configured:
        action_taken = "notify_only_no_broker"
        return WarningResponse(
            danger_zone=True,
            risk_warning=msg,
            distance_pct=round(distance * 100, 3),
            action_taken=action_taken,
            survival_odds_expire_otm=survival,
            protocol_notes=protocol_notes,
            notifications=notifications,
        )

    positions = await fetch_alpaca_positions(settings)
    spreads = find_put_credit_spreads_in_positions(
        positions,
        warning.ticker,
        short_strike=warning.short_strike,
        long_strike=warning.long_strike,
    )
    positions_matched = len(spreads)

    if not spreads:
        action_taken = "danger_no_matching_position"
        await notify(
            settings,
            "Warning — No Spread Found",
            f"{msg}\nNo open put credit spread matched short strike ${warning.short_strike:.2f}.",
            "WARNING",
        )
        return WarningResponse(
            danger_zone=True,
            risk_warning=msg,
            distance_pct=round(distance * 100, 3),
            action_taken=action_taken,
            survival_odds_expire_otm=survival,
            protocol_notes=protocol_notes,
            positions_matched=0,
            notifications=notifications,
        )

    spread = spreads[0]
    close_debit = resolve_warning_close_debit(spread, settings, override_debit=warning.close_debit)
    leg_syms = {leg.symbol for leg in spread.legs}

    if settings.warning_cancel_resting_exits and not dry_run:
        n = await cancel_alpaca_open_orders_for_symbols(settings, leg_syms)
        if n:
            protocol_notes.append(f"Canceled {n} resting order(s) on spread legs before emergency close")

    close_payload = build_alpaca_close_payload(spread, close_debit)
    close_payload["time_in_force"] = "day"
    close_payload["_meta"] = {
        "warning_close": True,
        "close_debit": close_debit,
        "survival_odds": survival,
    }

    await notify(
        settings,
        "Warning — Auto-Close Submitting",
        f"{msg}\nClosing spread at ${close_debit:.2f} debit (multi-leg). Survival odds were {survival * 100:.1f}%.",
        "CRITICAL",
    )

    close_order = await submit_alpaca_order(settings, close_payload, dry_run=dry_run)
    action_taken = "auto_close_submitted" if close_order.success else "auto_close_failed"

    return WarningResponse(
        danger_zone=True,
        risk_warning=msg,
        distance_pct=round(distance * 100, 3),
        action_taken=action_taken,
        survival_odds_expire_otm=survival,
        protocol_notes=protocol_notes,
        close_order=close_order,
        positions_matched=positions_matched,
        notifications=notifications,
    )
