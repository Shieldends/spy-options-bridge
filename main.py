"""
spy-options-bridge v5.5.28 — ALPACA PAPER (default broker)

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
  GET  /health          — shows broker=alpaca when configured
  GET  /activity        — today's webhook timeline (entry / warning / skips)
  POST /entry           — Alpaca mleg entry + GTC take-profit + GTC stop-loss
  POST /webhook         — alias for /entry
  POST /warning         — danger zone: notify + optional auto-close spread
  POST /close-put       — conservative batched buy-to-close for short puts
  POST /exercise/entry  — sync paper fill test (exercise mode, chase fill)
  POST /exercise/burst  — paper-only N-fill burst (?count=N or burstCount)
  POST /webhook/stx-close — STX strategy poll/evaluate/execute (Stage 2 automation)
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from collections import deque
from datetime import date, datetime, timedelta
from enum import Enum
from functools import lru_cache
from math import floor
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
from pathlib import Path
from email_alerts import send_email_alert

_scripts_dir = Path(__file__).resolve().parent / "scripts"
if _scripts_dir.is_dir() and str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))
from team_email import bridge_notify as team_bridge_notify  # noqa: E402
from spread_guards import check_spread_entry_allowed  # noqa: E402
from paper_spread_legs import (  # noqa: E402
    close_paper_spread_legs,
    open_crush_it_short_put,
    should_use_paper_spread_legs,
    submit_paper_spread_entry,
)

try:
    from dataclasses import asdict

    from stx_common import (  # noqa: E402
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
        read_state,
        underlying_legs,
        write_state,
    )
    from stx_watcher import extract_quote, midpoint_of  # noqa: E402

    _STX_MODULES_OK = True
except ImportError:
    asdict = None  # type: ignore[assignment,misc]
    _STX_MODULES_OK = False

from fastapi import BackgroundTasks, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_app_started_mono = time.monotonic()
_preflight_cache: dict[str, Any] = {"ts": 0.0, "data": {}}
_burst_in_progress = False
_chase_semaphore: asyncio.Semaphore | None = None
PREFLIGHT_CACHE_SEC = 30.0
BURST_ATTEMPTS_RESPONSE_CAP = 5
PREFLIGHT_TIMEOUT_SEC = 4.0
EXCHANGE_TZ = ZoneInfo("America/New_York")
ET = EXCHANGE_TZ  # backward-compatible alias for tests and imports


def now_exchange() -> datetime:
    """Current US equity session clock (America/New_York), always timezone-aware."""
    return datetime.now(EXCHANGE_TZ)


def as_exchange_time(when: datetime | None = None) -> datetime:
    """
    Normalize any clock input to America/New_York for market-time logic.

    Naive datetimes are treated as workstation local wall time, then converted to ET.
    Aware datetimes are converted to ET regardless of source zone.
    """
    if when is None:
        return now_exchange()
    if when.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo
        return when.replace(tzinfo=local_tz).astimezone(EXCHANGE_TZ)
    return when.astimezone(EXCHANGE_TZ)


def log_timezone_audit() -> None:
    """Boot audit: workstation local vs exchange (America/New_York) clock."""
    local_now = datetime.now().astimezone()
    exchange_now = now_exchange()
    logger.info(
        "TIMEZONE AUDIT | workstation_local=%s (%s) | exchange_America/New_York=%s (%s)",
        local_now.strftime("%Y-%m-%d %H:%M:%S"),
        local_now.tzname() or str(local_now.tzinfo),
        exchange_now.strftime("%Y-%m-%d %H:%M:%S"),
        exchange_now.tzname() or "America/New_York",
    )


_activity_log: deque[dict[str, Any]] = deque(maxlen=250)
_ACTIVITY_LOG_PATH = Path(__file__).resolve().parent / "logs" / "activity.jsonl"
CERT_URL = "https://api.cert.tastyworks.com"
ALPACA_PAPER_URL = "https://paper-api.alpaca.markets"


def record_activity(
    kind: Literal["entry", "warning", "close-put", "stx-close", "sniper-grid"],
    outcome: str,
    message: str,
    *,
    ticker: str = "SPY",
    extra: dict[str, Any] | None = None,
) -> None:
    """Ring buffer + append-only file for /activity (file survives Render restart)."""
    row: dict[str, Any] = {
        "ts_et": now_exchange().strftime("%Y-%m-%d %H:%M:%S ET"),
        "kind": kind,
        "outcome": outcome,
        "message": message[:500],
        "ticker": ticker,
    }
    if extra:
        row.update(extra)
    _activity_log.append(row)
    try:
        _ACTIVITY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _ACTIVITY_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except Exception as exc:
        logger.debug("activity file append skipped: %s", exc)


SNIPER_LEG_TELEMETRY_MAP: dict[str, str] = {"A": "leg_a", "B": "leg_b", "C": "leg_c"}


def build_sniper_signal_id(underlying: str, expiration: str, short_strike: float) -> str:
    """Stable id for backend reconciler hydration (underlying + exp + strike + deploy time ET)."""
    ts = now_exchange().strftime("%Y%m%dT%H%M%S")
    return f"{underlying.upper()}|{expiration}|{short_strike:.2f}|{ts}"


def protective_hedge_telemetry_from_traps(traps: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map trap legs A/B/C to reconciler protective_hedge leg_a/leg_b/leg_c keys."""
    by_leg = {str(t.get("leg", "")).upper(): t for t in traps}
    out: dict[str, dict[str, Any]] = {}
    for leg_code, key in SNIPER_LEG_TELEMETRY_MAP.items():
        trap = by_leg.get(leg_code)
        if trap and trap.get("order_id"):
            strike_raw = trap.get("hedge_strike")
            out[key] = {
                "order_id": str(trap["order_id"]),
                "strike": float(strike_raw) if strike_raw is not None else None,
                "status": "pending",
            }
        else:
            out[key] = {"order_id": None, "strike": None, "status": "failed"}
    return out


def append_activity_telemetry(row: dict[str, Any]) -> None:
    """Append reconciler-facing JSON line to logs/activity.jsonl."""
    try:
        _ACTIVITY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _ACTIVITY_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except Exception as exc:
        logger.warning("activity telemetry append failed: %s", exc)


def record_protective_hedge_telemetry(
    traps: list[dict[str, Any]],
    *,
    underlying: str,
    expiration: str,
    short_strike: float,
    signal_id: str | None = None,
) -> str:
    """
    Log protective hedge deploy row for backend startup reconciler (PR #51 schema).

    Written immediately after Legs A/B/C asyncio.gather completes.
    """
    sid = signal_id or build_sniper_signal_id(underlying, expiration, short_strike)
    row = {
        "timestamp": now_exchange().isoformat(timespec="seconds"),
        "signal_id": sid,
        "protective_hedge": protective_hedge_telemetry_from_traps(traps),
    }
    append_activity_telemetry(row)
    logger.info("Protective hedge telemetry logged signal_id=%s", sid)
    return sid


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
    default_dte_filter: str = Field(default="0dte", alias="DEFAULT_DTE_FILTER")
    alpaca_exit_fill_timeout: int = Field(default=600, alias="ALPACA_EXIT_FILL_TIMEOUT")
    alpaca_exit_poll_seconds: float = Field(default=3.0, alias="ALPACA_EXIT_POLL_SECONDS")
    auto_chase_entry_fill: bool = Field(default=True, alias="AUTO_CHASE_ENTRY_FILL")
    entry_chase_wait_seconds: float = Field(default=4.0, alias="ENTRY_CHASE_WAIT_SECONDS")
    entry_chase_poll_seconds: float = Field(default=1.5, alias="ENTRY_CHASE_POLL_SECONDS")
    entry_chase_max_attempts: int = Field(default=25, alias="ENTRY_CHASE_MAX_ATTEMPTS")
    entry_chase_floor_extra_polls: int = Field(default=15, alias="ENTRY_CHASE_FLOOR_EXTRA_POLLS")
    entry_min_credit: float = Field(default=0.01, alias="ENTRY_MIN_CREDIT")
    paper_force_min_fill: bool = Field(default=True, alias="PAPER_FORCE_MIN_FILL")
    auto_cancel_conflicting_orders: bool = Field(
        default=True, alias="AUTO_CANCEL_CONFLICTING_ORDERS"
    )

    discord_webhook_url: str = Field(default="", alias="DISCORD_WEBHOOK_URL")
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    email_enabled: bool = Field(default=False, alias="EMAIL_ENABLED")
    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    email_from: str = Field(default="", alias="EMAIL_FROM")
    email_to: str = Field(default="shieldinc850@gmail.com", alias="EMAIL_TO")

    default_underlying: str = Field(default="SPY", alias="DEFAULT_UNDERLYING")
    default_quantity: int = Field(default=1, alias="DEFAULT_QUANTITY")
    default_strike_offset_short: int = Field(default=-10, alias="DEFAULT_STRIKE_OFFSET_SHORT")
    default_strike_offset_long: int = Field(default=-15, alias="DEFAULT_STRIKE_OFFSET_LONG")
    default_limit_credit: float = Field(default=0.45, alias="DEFAULT_LIMIT_CREDIT")
    default_fill_mode: str = Field(default="exercise", alias="DEFAULT_FILL_MODE")
    # fixed | auto | aggressive | exercise | fill — exercise/fill lean low for paper fills
    max_quantity: int = Field(default=0, alias="MAX_QUANTITY")
    # 0 = no cap; set e.g. 10 to limit spreads per alert
    burst_max_count: int = Field(default=10, alias="BURST_MAX_COUNT")
    max_concurrent_chase_tasks: int = Field(default=2, alias="MAX_CONCURRENT_CHASE_TASKS")

    spread_min_credit: float = Field(default=0.40, alias="SPREAD_MIN_CREDIT")
    spread_max_trades_per_day: int = Field(default=0, alias="SPREAD_MAX_TRADES_PER_DAY")
    spread_daily_loss_limit: float = Field(default=0.0, alias="SPREAD_DAILY_LOSS_LIMIT")
    spread_mode_only: bool = Field(default=True, alias="SPREAD_MODE_ONLY")

    stx_sniper_grid_enabled: bool = Field(default=True, alias="STX_SNIPER_GRID_ENABLED")
    stx_hedge_strike_offset: float = Field(default=-10.0, alias="STX_HEDGE_STRIKE_OFFSET")
    sniper_marinate_step: float = Field(default=0.01, alias="SNIPER_MARINATE_STEP")
    sniper_chase_seconds: float = Field(default=15.0, alias="SNIPER_CHASE_SECONDS")

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

    @property
    def email_configured(self) -> bool:
        return bool(
            self.email_enabled and self.smtp_host and self.email_from and self.email_to
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


# ── Models ────────────────────────────────────────────────────────────────────


class SpreadStrategy(str, Enum):
    PUT_CREDIT_SPREAD = "put_credit_spread"
    CALL_CREDIT_SPREAD = "call_credit_spread"
    SHORT_PUT = "short_put"


class TradingViewSignal(BaseModel):
    ticker: str
    strategy: SpreadStrategy | None = None
    signal_price: float | None = Field(default=None, alias="signalPrice")
    quantity: int = 1
    strike_offset_short: int = Field(default=-10, alias="strikeOffsetShort")
    strike_offset_long: int = Field(default=-15, alias="strikeOffsetLong")
    short_strike: float | None = Field(default=None, alias="short_strike")
    long_strike: float | None = Field(default=None, alias="long_strike")
    limit_credit: float | None = Field(default=None, alias="limitCredit")
    fill_mode: str | None = Field(default=None, alias="fillMode")
    expiration: str = "0dte"
    dte_filter: str | None = Field(default=None, alias="dteFilter")
    action: str = "enter"
    entry_batch_size: int | None = Field(default=None, alias="entryBatchSize")

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
        if self.action in {"SHORT_PUT", "SHORT PUT", "SELL_PUT", "SELL PUT"}:
            self.strategy = SpreadStrategy.SHORT_PUT
        elif self.action in {"PUT_CREDIT_SPREAD", "PUT CREDIT SPREAD"}:
            self.strategy = SpreadStrategy.PUT_CREDIT_SPREAD
        elif self.action in {"CALL_CREDIT_SPREAD", "CALL CREDIT SPREAD"}:
            self.strategy = SpreadStrategy.CALL_CREDIT_SPREAD
        elif self.strategy is None:
            self.strategy = SpreadStrategy.PUT_CREDIT_SPREAD
        if self.strategy == SpreadStrategy.SHORT_PUT:
            if self.short_strike is None and self.signal_price is None:
                raise ValueError("SHORT_PUT requires short_strike or signalPrice")
            return self
        if self.short_strike is not None and self.long_strike is not None:
            return self
        if self.signal_price is None:
            raise ValueError("Provide signalPrice OR both short_strike and long_strike")
        return self

    @property
    def uses_explicit_strikes(self) -> bool:
        return self.short_strike is not None and self.long_strike is not None

    @property
    def is_short_put(self) -> bool:
        return self.strategy == SpreadStrategy.SHORT_PUT


class ClosePutSignal(BaseModel):
    """Conservative batched buy-to-close for naked short puts."""

    ticker: str
    short_strike: float | None = Field(default=None, alias="short_strike")
    expiration: str | None = None
    dte_filter: str | None = Field(default=None, alias="dteFilter")
    quantity: int | None = None
    batch_size: int = Field(default=6, alias="batchSize")
    bid_premium: float = Field(default=0.03, alias="bidPremium")
    chase_step: float = Field(default=0.02, alias="chaseStep")
    max_chase_steps: int = Field(default=3, alias="maxChaseSteps")
    close_mode: str = Field(default="conservative", alias="closeMode")

    model_config = {"populate_by_name": True}

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper().replace(" ", "")

    @field_validator("batch_size", "quantity", mode="before")
    @classmethod
    def coerce_positive_int(cls, value: Any) -> int | None:
        if value is None or value == "":
            return None
        return max(int(float(value)), 1)


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


class StxCloseSignal(BaseModel):
    """Inbound STX close / poll signal (TradingView or external automation)."""

    underlying: str = "STX"
    expiration: str | None = None
    strike: float | None = None
    strike_offset: float | None = Field(default=None, alias="strikeOffset")
    signal_price: float | None = Field(default=None, alias="signalPrice")
    hedge_strike_offset: float | None = Field(default=None, alias="hedgeStrikeOffset")
    enable_put_walker: bool | None = Field(default=None, alias="enablePutWalker")
    enable_sniper_grid: bool | None = Field(default=None, alias="enableSniperGrid")
    dte_filter: str | None = Field(default=None, alias="dteFilter")
    option_type: str = Field(default="put", alias="type")
    mode: Literal["poll", "evaluate", "execute", "open"] = "evaluate"
    confirm_close: bool = Field(default=False, alias="confirmClose")
    confirm_open: bool = Field(default=False, alias="confirmOpen")
    quantity: int = Field(default=1, ge=1)
    prev_close_iv: float | None = Field(default=None, alias="prevCloseIv")
    poll_seconds: int = Field(default=15, alias="pollSeconds")

    model_config = {"populate_by_name": True}

    @field_validator("underlying", mode="before")
    @classmethod
    def normalize_underlying(cls, value: str) -> str:
        return str(value).upper().replace(" ", "")

    @field_validator("option_type", mode="before")
    @classmethod
    def normalize_option_type(cls, value: str) -> str:
        raw = str(value).lower().strip()
        if raw not in ("put", "call"):
            raise ValueError("type must be put or call")
        return raw


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
    now = as_exchange_time(now)
    spec = (dte_filter or expiration or "0dte").strip().lower()

    if spec in {"0dte", "today", "+0 days", "+0 day", "1dte", ""}:
        return now.strftime("%Y-%m-%d")

    if spec in {"+1dte", "+1 day", "+1 days", "2dte", "tomorrow"}:
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")

    if spec in {"+2dte", "+2 days", "+2 day"}:
        return (now + timedelta(days=2)).strftime("%Y-%m-%d")

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


def build_short_put_package(signal: TradingViewSignal, settings: Settings) -> SpreadPackage:
    expiration = resolve_dte_expiration(signal.expiration, signal.dte_filter or settings.default_dte_filter)
    if signal.short_strike is not None:
        short_strike = float(signal.short_strike)
    else:
        atm = _round_strike(signal.signal_price)  # type: ignore[arg-type]
        short_strike = atm + signal.strike_offset_short

    limit_credit = signal.limit_credit if signal.limit_credit is not None else settings.default_limit_credit
    put_sym = format_occ_symbol(signal.ticker, expiration, "put", short_strike)

    return SpreadPackage(
        qty=str(signal.quantity),
        legs=[SpreadLeg(symbol=put_sym, side="sell", position_intent="sell_to_open")],
        metadata={
            "underlying": signal.ticker,
            "strategy": SpreadStrategy.SHORT_PUT.value,
            "expiration": expiration,
            "short_strike": short_strike,
            "limit_credit": limit_credit,
            "single_leg": True,
            "dte_filter": signal.dte_filter or settings.default_dte_filter,
            "entry_batch_size": signal.entry_batch_size,
        },
    )


def build_order_from_signal(signal: TradingViewSignal, settings: Settings) -> SpreadPackage:
    if signal.is_short_put:
        return build_short_put_package(signal, settings)
    return build_spread(signal, settings)


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


async def fetch_alpaca_underlying_price(settings: Settings, underlying: str) -> float | None:
    """Latest trade price from Alpaca data API."""
    headers = {
        "Apca-Api-Key-Id": settings.alpaca_key,
        "Apca-Api-Secret-Key": settings.alpaca_secret,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"https://data.alpaca.markets/v2/stocks/{underlying.upper()}/trades/latest",
            headers=headers,
        )
    if not r.is_success:
        return None
    p = r.json().get("trade", {}).get("p")
    return float(p) if p is not None else None


async def fetch_alpaca_nearest_option_expiry(
    settings: Settings,
    underlying: str,
    option_type: str,
) -> str | None:
    """Nearest future expiration Alpaca actually lists (not calendar Friday guess)."""
    base = settings.apca_api_base_url.rstrip("/")
    headers = {
        "Apca-Api-Key-Id": settings.alpaca_key,
        "Apca-Api-Secret-Key": settings.alpaca_secret,
    }
    params = {
        "underlying_symbols": underlying.upper(),
        "status": "active",
        "type": option_type,
        "limit": 500,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{base}/v2/options/contracts", headers=headers, params=params)
    if not r.is_success:
        return None

    today = now_exchange().date()
    expiries: set[date] = set()
    for row in r.json().get("option_contracts") or []:
        raw = str(row.get("expiration_date") or row.get("expiration") or "")[:10]
        if not raw:
            continue
        exp = datetime.strptime(raw, "%Y-%m-%d").date()
        if exp >= today:
            expiries.add(exp)
    if not expiries:
        return None
    return min(expiries).strftime("%Y-%m-%d")


async def fetch_alpaca_options_buying_power(settings: Settings) -> float | None:
    base = settings.apca_api_base_url.rstrip("/")
    headers = {
        "Apca-Api-Key-Id": settings.alpaca_key,
        "Apca-Api-Secret-Key": settings.alpaca_secret,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(f"{base}/v2/account", headers=headers)
    if not r.is_success:
        return None
    raw = r.json().get("options_buying_power") or r.json().get("buying_power")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def cap_put_strike_for_buying_power(
    strike: float,
    available: list[float],
    options_buying_power: float,
    quantity: int,
) -> tuple[float, bool]:
    """Cash-secured put collateral ≈ strike × 100 × qty — pick highest listed strike that fits."""
    if options_buying_power <= 0 or quantity < 1:
        return strike, False
    max_strike = floor(options_buying_power / (100 * quantity)) - 5.0
    if strike <= max_strike:
        return strike, False
    fits = [s for s in available if s <= max_strike]
    if not fits:
        raise ValueError(
            f"No STX put strike within options buying power "
            f"(${options_buying_power:,.0f} — need strike ≤ ${max_strike:,.0f})"
        )
    return max(fits), True


async def verify_alpaca_option_contract(settings: Settings, symbol: str) -> bool:
    base = settings.apca_api_base_url.rstrip("/")
    headers = {
        "Apca-Api-Key-Id": settings.alpaca_key,
        "Apca-Api-Secret-Key": settings.alpaca_secret,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(f"{base}/v2/options/contracts/{symbol}", headers=headers)
    return r.is_success


async def resolve_stx_open_contract(settings: Settings, signal: StxCloseSignal) -> dict[str, Any]:
    """
    Map Crush-It open to a real Alpaca-listed contract.

    Uses Alpaca's nearest expiry + snaps strike to the chain. Ignores stale JSON
    strikes (e.g. 230 when STX trades ~1000+). Prefer strikeOffset + signalPrice.
    """
    underlying = signal.underlying.upper()
    option_type = signal.option_type

    expiration = await fetch_alpaca_nearest_option_expiry(settings, underlying, option_type)
    if not expiration:
        expiration = _resolve_stx_expiration(signal)
        logger.warning("STX: Alpaca expiry lookup empty — fallback %s", expiration)

    available = await fetch_alpaca_option_strikes(settings, underlying, expiration, option_type)
    if not available:
        raise ValueError(f"No Alpaca {option_type} strikes for {underlying} exp {expiration}")

    ref_price = signal.signal_price
    if ref_price is None:
        ref_price = await fetch_alpaca_underlying_price(settings, underlying)
    if ref_price is None:
        ref_price = available[len(available) // 2]

    meta: dict[str, Any] = {
        "underlying_price": ref_price,
        "expiration_resolved": expiration,
        "strikes_available": len(available),
    }

    default_otm = -10.0
    if signal.strike_offset is not None:
        target = _round_strike(float(ref_price) + float(signal.strike_offset))
        meta["strike_source"] = "strikeOffset"
    elif signal.strike is not None:
        target = float(signal.strike)
        meta["strike_source"] = "json_strike"
        if ref_price and abs(target - ref_price) / ref_price > 0.25:
            target = _round_strike(float(ref_price) + default_otm)
            meta["strike_source"] = "json_strike_stale_ignored"
            logger.warning(
                "STX strike %.2f ignored (ref %.2f) — using OTM %.2f",
                signal.strike,
                ref_price,
                target,
            )
    else:
        target = _round_strike(float(ref_price) + default_otm)
        meta["strike_source"] = "default_otm"

    meta["strike_target"] = target
    if option_type == "put":
        strike = snap_short_put_strike(target, available)
        obp = await fetch_alpaca_options_buying_power(settings)
        if obp:
            meta["options_buying_power"] = obp
            strike, capped = cap_put_strike_for_buying_power(strike, available, obp, signal.quantity)
            if capped:
                meta["strike_capped_for_buying_power"] = True
                logger.info("STX strike capped to %.2f (options BP $%.0f)", strike, obp)
    else:
        above = [s for s in available if s >= target]
        strike = min(above) if above else min(available, key=lambda s: abs(s - target))

    symbol = format_occ_symbol(underlying, expiration, option_type, strike)
    meta["symbol"] = symbol
    meta["strike_snapped"] = strike

    if not await verify_alpaca_option_contract(settings, symbol):
        raise ValueError(f"Alpaca contract not found after snap: {symbol}")

    return {"expiration": expiration, "strike": strike, "symbol": symbol, "meta": meta}


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
    if spread.metadata.get("single_leg") or len(spread.legs) == 1:
        short_sym = spread.legs[0].symbol.upper()
        short_q = quotes.get(short_sym, {})
        short_bid = short_q.get("bid", 0.0)
        meta: dict[str, Any] = {"short_bid": short_bid, "fill_mode": mode, "quote_source": "single_leg"}
        if short_bid <= 0:
            fallback = _quote_fallback_credit(cap)
            meta["quote_source"] = "fallback_no_quotes"
            return fallback, meta
        if mode == "aggressive":
            credit = max(short_bid * 0.85 - 0.02, 0.05)
        elif mode == "exercise":
            credit = max(short_bid * 0.55 - 0.05, 0.05)
        else:
            credit = max(short_bid * 0.95, 0.05)
        credit = round(credit, 2)
        if cap is not None and cap > 0:
            credit = min(credit, round(cap, 2))
            meta["cap_applied"] = cap
        meta["limit_credit_final"] = credit
        return credit, meta

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

    is_spread = spread.metadata.get("strategy") == "put_credit_spread"
    pin_paper_credit = (
        settings.use_alpaca
        and settings.is_alpaca_paper
        and (
            settings.paper_force_min_fill
            or is_spread
            or spread.metadata.get("strategy") == "short_put"
        )
    )

    # Alpaca paper: pin to low limit so simulator fills (spreads + crush-it shorts).
    if pin_paper_credit:
        credit = round(max(settings.entry_min_credit, 0.05), 2)
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
        if pin_paper_credit and is_spread:
            credit = round(settings.entry_min_credit, 2)
            meta["fill_mode_resolved"] = "paper_force_min"
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
    # Honor TV limitCredit when it meets spread policy — 0DTE quote estimates can
    # floor at $0.05 while the alert still requests a valid limit (e.g. $0.45).
    if (
        is_spread
        and cap is not None
        and cap >= settings.spread_min_credit
        and credit < cap
    ):
        credit = round(cap, 2)
        meta["quote_source"] = str(meta.get("quote_source", "")) + "+tv_limit"
    meta["limit_credit_final"] = credit
    if is_spread and settings.spread_min_credit > 0 and credit < settings.spread_min_credit:
        raise ValueError(
            f"Estimated credit ${credit:.2f} below minimum ${settings.spread_min_credit:.2f} — skip trade"
        )
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


def snap_short_put_strike(target: float, available: list[float]) -> float:
    """Pick nearest listed put strike at or below target (OTM short put)."""
    if not available:
        return target
    candidates = [s for s in available if s <= target]
    if candidates:
        return max(candidates)
    return min(available, key=lambda s: abs(s - target))


async def align_short_put_to_alpaca(
    settings: Settings,
    spread: SpreadPackage,
    signal: TradingViewSignal,
) -> SpreadPackage:
    expiration = str(spread.metadata["expiration"])
    underlying = str(spread.metadata["underlying"])
    target = float(spread.metadata["short_strike"])
    available = await fetch_alpaca_option_strikes(settings, underlying, expiration, "put")
    if not available:
        logger.warning("No Alpaca strikes for %s %s short put — using computed strike", underlying, expiration)
        return spread

    snapped = snap_short_put_strike(target, available)
    if snapped != target:
        logger.info("Snapped %s short put strike %.2f→%.2f", underlying, target, snapped)

    put_sym = format_occ_symbol(underlying, expiration, "put", snapped)
    return SpreadPackage(
        qty=spread.qty,
        legs=[SpreadLeg(symbol=put_sym, side="sell", position_intent="sell_to_open")],
        metadata={**spread.metadata, "short_strike": snapped, "strikes_snapped": snapped != target},
    )


async def align_spread_to_alpaca(settings: Settings, spread: SpreadPackage, signal: TradingViewSignal) -> SpreadPackage:
    """Snap strikes to Alpaca-listed contracts and rebuild OCC symbols."""
    if spread.metadata.get("single_leg"):
        return await align_short_put_to_alpaca(settings, spread, signal)
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


def build_alpaca_single_leg_payload(
    spread: SpreadPackage,
    *,
    limit_price: float,
    opening: bool,
    time_in_force: str = "day",
) -> dict:
    """Alpaca single-leg option order (naked short put or buy-to-close)."""
    leg = spread.legs[0]
    if opening:
        return {
            "symbol": leg.symbol,
            "qty": spread.qty,
            "side": "sell",
            "type": "limit",
            "limit_price": f"{round(limit_price, 2):.2f}",
            "time_in_force": time_in_force,
        }
    return {
        "symbol": leg.symbol,
        "qty": spread.qty,
        "side": "buy",
        "type": "limit",
        "limit_price": f"{round(limit_price, 2):.2f}",
        "time_in_force": time_in_force,
    }


def build_alpaca_entry_payload(spread: SpreadPackage) -> dict:
    credit = float(spread.metadata["limit_credit"])
    if spread.metadata.get("single_leg"):
        return build_alpaca_single_leg_payload(spread, limit_price=credit, opening=True, time_in_force="day")
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


def find_short_puts_in_positions(
    positions: list[dict[str, Any]],
    ticker: str,
    *,
    short_strike: float | None = None,
    expiration: str | None = None,
    strike_tolerance: float = 0.51,
) -> list[dict[str, Any]]:
    """Return open short put positions (qty < 0) for ticker."""
    ticker = ticker.upper()
    matches: list[dict[str, Any]] = []
    for p in positions:
        sym = str(p.get("symbol", ""))
        meta = parse_occ_symbol(sym)
        if not meta or meta["underlying"] != ticker or meta["option_type"] != "put":
            continue
        try:
            qty = int(float(p.get("qty", 0)))
        except (TypeError, ValueError):
            continue
        if qty >= 0:
            continue
        if short_strike is not None and abs(meta["strike"] - short_strike) > strike_tolerance:
            continue
        if expiration and meta.get("expiration") != expiration:
            continue
        matches.append({**p, "_meta": meta, "_qty": abs(qty)})
    return matches


def split_batches(total_qty: int, batch_size: int) -> list[int]:
    size = max(batch_size, 1)
    batches: list[int] = []
    remaining = total_qty
    while remaining > 0:
        chunk = min(size, remaining)
        batches.append(chunk)
        remaining -= chunk
    return batches


def conservative_close_limit(bid: float, bid_premium: float) -> float:
    """Limit slightly above bid — faster fill, conservative style."""
    if bid <= 0:
        return round(bid_premium + 0.05, 2)
    return round(bid + bid_premium, 2)


async def fetch_alpaca_positions(settings: Settings) -> list[dict[str, Any]]:
    base = settings.apca_api_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{base}/v2/positions", headers=_alpaca_headers(settings))
    if not r.is_success:
        logger.warning("Alpaca positions fetch failed: %s", r.text[:200])
        return []
    data = r.json()
    return data if isinstance(data, list) else []


async def cancel_alpaca_order(settings: Settings, order_id: str) -> bool:
    """Cancel a single Alpaca order by id."""
    if not order_id:
        return False
    base = settings.apca_api_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.delete(f"{base}/v2/orders/{order_id}", headers=_alpaca_headers(settings))
    if r.is_success:
        logger.info("Canceled Alpaca order %s", order_id)
        return True
    logger.warning("Cancel order %s failed (%s): %s", order_id, r.status_code, r.text[:200])
    return False


async def fetch_alpaca_open_orders(settings: Settings, *, limit: int = 100) -> list[dict[str, Any]]:
    """List open Alpaca orders (nested legs when present)."""
    if not settings.alpaca_configured:
        return []
    base = settings.apca_api_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"{base}/v2/orders",
            headers=_alpaca_headers(settings),
            params={"status": "open", "limit": limit, "nested": "true"},
        )
    if not r.is_success:
        logger.warning("Alpaca open-order list failed (%s): %s", r.status_code, r.text[:200])
        return []
    body = r.json()
    return body if isinstance(body, list) else []


async def cancel_alpaca_open_orders_for_symbols(settings: Settings, symbols: set[str]) -> int:
    """Cancel resting orders whose legs intersect symbol set (TP/SL or entry conflicts)."""
    if not symbols:
        return 0
    canceled = 0
    base = settings.apca_api_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        for order in await fetch_alpaca_open_orders(settings):
            legs = order.get("legs") or []
            leg_syms = {str(leg.get("symbol", "")) for leg in legs}
            sym = order.get("symbol")
            if sym:
                leg_syms.add(str(sym))
            if not leg_syms.intersection(symbols):
                continue
            oid = order.get("id")
            if not oid:
                continue
            cr = await client.delete(f"{base}/v2/orders/{oid}", headers=_alpaca_headers(settings))
            if cr.is_success:
                canceled += 1
    return canceled


async def cancel_open_mleg_orders(settings: Settings) -> int:
    """Cancel all open multi-leg orders (burst leftovers block new mleg entries)."""
    canceled = 0
    base = settings.apca_api_base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        for order in await fetch_alpaca_open_orders(settings):
            if str(order.get("order_class", "")).lower() != "mleg":
                continue
            oid = order.get("id")
            if not oid:
                continue
            cr = await client.delete(f"{base}/v2/orders/{oid}", headers=_alpaca_headers(settings))
            if cr.is_success:
                canceled += 1
    return canceled


async def alpaca_open_order_preflight(settings: Settings) -> dict[str, int]:
    """Counts for /health TV-pause risk (no secrets)."""
    orders = await fetch_alpaca_open_orders(settings)
    mleg = sum(1 for o in orders if str(o.get("order_class", "")).lower() == "mleg")
    return {
        "open_order_count": len(orders),
        "open_mleg_count": mleg,
    }


async def alpaca_open_order_preflight_cached(settings: Settings) -> dict[str, Any]:
    """Cached open-order counts so /health stays fast for keepalive probes."""
    now = time.monotonic()
    if now - float(_preflight_cache["ts"]) < PREFLIGHT_CACHE_SEC:
        cached = _preflight_cache.get("data")
        if isinstance(cached, dict):
            return cached
    try:
        data = await asyncio.wait_for(
            alpaca_open_order_preflight(settings),
            timeout=PREFLIGHT_TIMEOUT_SEC,
        )
    except Exception as exc:
        logger.warning("Alpaca preflight skipped: %s", exc)
        data = {"open_order_count": -1, "open_mleg_count": -1, "preflight_skipped": True}
    _preflight_cache["ts"] = now
    _preflight_cache["data"] = data
    return data


def build_tv_pause_risk(settings: Settings, preflight: dict[str, int]) -> dict[str, Any]:
    """Surface signals that can pause TradingView alerts or block fills."""
    reasons: list[str] = []
    if not settings.configured:
        reasons.append("bridge_not_configured")
    if not settings.webhook_secret:
        reasons.append("webhook_secret_missing_on_bridge")
    open_mleg = int(preflight.get("open_mleg_count", 0))
    open_total = int(preflight.get("open_order_count", 0))
    if preflight.get("preflight_skipped"):
        reasons.append("alpaca_preflight_skipped")
    elif open_mleg > 0:
        reasons.append(f"open_mleg_orders={open_mleg}")
    elif open_total > 0:
        reasons.append(f"open_orders={open_total}")
    uptime_sec = round(time.monotonic() - _app_started_mono, 1)
    if uptime_sec < 45:
        reasons.append(f"cold_start_uptime_sec={uptime_sec}")
    if any(r.startswith("bridge_not") or r.startswith("webhook_secret") for r in reasons):
        level = "red"
    elif reasons:
        level = "yellow"
    else:
        level = "green"
    return {
        "level": level,
        "reasons": reasons,
        "webhook_secret_configured": bool(settings.webhook_secret),
        "open_mleg_count": open_mleg,
        "open_order_count": open_total,
        "uptime_sec": uptime_sec,
        "auto_cancel_conflicting_orders": settings.auto_cancel_conflicting_orders,
    }


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


_EMAIL_SKIP_TITLES = frozenset({"Chasing Entry Fill"})


async def notify(
    settings: Settings,
    title: str,
    body: str,
    level: str = "INFO",
    *,
    send_email: bool = True,
) -> dict:
    message = f"**[{level}] {title}**\n{body}"
    results: dict = {}

    if (
        send_email
        and title not in _EMAIL_SKIP_TITLES
        and settings.email_configured
    ):
        plain = f"[{level}] {title}\n{body}"
        try:
            ok = await asyncio.to_thread(
                team_bridge_notify,
                title,
                plain,
                level=level,
                settings=settings,
            )
            if ok:
                results["email"] = "sent"
        except Exception as exc:
            logger.warning("Email notify failed: %s", exc)

    if not settings.discord_webhook_url and not settings.telegram_configured:
        if not results.get("email"):
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

    if settings.is_alpaca_paper and str(clean.get("order_class", "")).lower() == "mleg":
        return OrderResult(
            success=False,
            message="Alpaca paper blocks mleg — use paper spread legs (single-leg path)",
            dry_run=False,
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


async def replace_alpaca_order_limit_price(settings: Settings, order_id: str, limit_price: float) -> bool:
    """PATCH resting single-leg limit (sniper grid marination / chase)."""
    base = settings.apca_api_base_url.rstrip("/")
    payload = {"limit_price": f"{round(limit_price, 2):.2f}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.patch(f"{base}/v2/orders/{order_id}", headers=_alpaca_headers(settings), json=payload)
    if r.is_success:
        logger.info("Sniper grid: order %s limit -> $%s", order_id, limit_price)
        return True
    logger.warning("Sniper grid replace failed (%s): %s", r.status_code, r.text[:200])
    return False


async def alpaca_market_is_open(settings: Settings) -> bool:
    base = settings.apca_api_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(f"{base}/v2/clock", headers=_alpaca_headers(settings))
        if r.is_success:
            return bool(r.json().get("is_open"))
    except Exception as exc:
        logger.debug("Alpaca clock lookup skipped: %s", exc)
    now = now_exchange()
    return now.weekday() < 5 and (now.hour > 9 or (now.hour == 9 and now.minute >= 30)) and now.hour < 16


async def resolve_hedge_put_contract(
    settings: Settings,
    *,
    underlying: str,
    expiration: str,
    short_strike: float,
    hedge_offset: float,
    option_type: str = "put",
) -> dict[str, Any]:
    """Long protective leg: short_strike + hedge_offset (e.g. -10 for bull put spread)."""
    target = _round_strike(short_strike + hedge_offset)
    available = await fetch_alpaca_option_strikes(settings, underlying, expiration, option_type)
    if not available:
        raise ValueError(f"No Alpaca {option_type} strikes for hedge {underlying} {expiration}")

    if option_type == "put":
        strike = snap_short_put_strike(target, available)
    else:
        above = [s for s in available if s >= target]
        strike = min(above) if above else min(available, key=lambda s: abs(s - target))

    symbol = format_occ_symbol(underlying, expiration, option_type, strike)
    if not await verify_alpaca_option_contract(settings, symbol):
        raise ValueError(f"Hedge contract not listed: {symbol}")

    return {
        "underlying": underlying.upper(),
        "expiration": expiration,
        "short_strike": short_strike,
        "hedge_strike": strike,
        "hedge_offset": hedge_offset,
        "symbol": symbol,
        "option_type": option_type,
    }


# Multi-strike adaptive sniper grid trap definitions (offsets from short strike).
SNIPER_TRAP_SPECS: tuple[tuple[str, float, float], ...] = (
    ("A", -10.0, 0.08),  # Premium pocket
    ("B", -15.0, 0.04),  # Deep vacuum
    ("C", -20.0, 0.02),  # Stub floor
)


def passive_interval_seconds(now: datetime | None = None) -> float:
    """America/New_York marination cadence before force-fill phase."""
    now_et = as_exchange_time(now)
    minutes = now_et.hour * 60 + now_et.minute
    if minutes < 11 * 60 + 30:
        return 1200.0  # 20 min — morning spread
    if minutes < 14 * 60:
        return 600.0  # 10 min — midday drift
    return 600.0  # pre-chase fallback on non-expiration days


def is_expiration_day(expiration: str, now: datetime | None = None) -> bool:
    now_et = as_exchange_time(now)
    try:
        exp_date = datetime.strptime(expiration[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return now_et.date() == exp_date


def is_force_fill_phase(expiration: str, now: datetime | None = None) -> bool:
    """After 14:00 America/New_York on contract expiration day (< 2 hours to 16:00 close)."""
    now_et = as_exchange_time(now)
    if not is_expiration_day(expiration, now_et):
        return False
    return now_et.hour >= 14


async def fetch_hedge_chase_limit(settings: Settings, symbol: str, fallback: float) -> float:
    """Live ask, else bid/ask midpoint, else fallback."""
    quotes = await fetch_option_snapshot_quotes(settings, [symbol])
    q = quotes.get(symbol.upper(), {})
    bid = float(q.get("bid") or 0.0)
    ask = float(q.get("ask") or 0.0)
    if ask > 0:
        return round(ask, 2)
    if bid > 0 and ask > 0:
        return round((bid + ask) / 2.0, 2)
    if bid > 0:
        return round(bid + 0.01, 2)
    return round(fallback, 2)


async def _submit_sniper_trap_leg(
    settings: Settings,
    *,
    underlying: str,
    expiration: str,
    short_strike: float,
    quantity: int,
    leg: str,
    hedge_offset: float,
    start_limit: float,
    option_type: str,
    dry_run: bool,
) -> dict[str, Any] | None:
    try:
        hedge = await resolve_hedge_put_contract(
            settings,
            underlying=underlying,
            expiration=expiration,
            short_strike=short_strike,
            hedge_offset=hedge_offset,
            option_type=option_type,
        )
    except Exception as exc:
        logger.warning("Sniper trap %s resolve failed: %s", leg, exc)
        return None

    payload = {
        "symbol": hedge["symbol"],
        "qty": str(quantity),
        "side": "buy",
        "type": "limit",
        "limit_price": f"{start_limit:.2f}",
        "time_in_force": "gtc",
    }
    result = await submit_order(settings, payload, dry_run=dry_run)
    if not result.success:
        logger.warning("Sniper trap %s submit failed: %s", leg, result.message)
        return None

    order_id = str((result.broker_response or {}).get("id", ""))
    if not order_id:
        return None

    return {
        "leg": leg,
        "order_id": order_id,
        "symbol": hedge["symbol"],
        "hedge_strike": hedge["hedge_strike"],
        "hedge_offset": hedge_offset,
        "limit_price": start_limit,
        "filled": False,
    }


async def _trap_order_open(settings: Settings, trap: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    """Return (still_open, order_dict)."""
    terminal = {"canceled", "expired", "rejected", "failed", "done_for_day", "filled"}
    order = await fetch_alpaca_order(settings, trap["order_id"])
    status = str(order.get("status", "")).lower()
    filled_qty = float(order.get("filled_qty") or 0)
    if status == "filled" or filled_qty > 0:
        trap["filled"] = True
        trap["filled_avg_price"] = order.get("filled_avg_price")
        return False, order
    if status in terminal:
        return False, order
    if order.get("limit_price") is not None:
        try:
            trap["limit_price"] = float(order["limit_price"])
        except (TypeError, ValueError):
            pass
    return True, order


async def _cancel_open_traps(settings: Settings, traps: list[dict[str, Any]], *, skip_legs: set[str] | None = None) -> None:
    skip = skip_legs or set()
    for trap in traps:
        if trap.get("filled") or trap["leg"] in skip:
            continue
        await cancel_alpaca_order(settings, trap["order_id"])


async def run_sniper_grid_loop(
    settings: Settings,
    *,
    underlying: str,
    expiration: str,
    short_strike: float,
    quantity: int,
    option_type: str = "put",
    signal_id: str | None = None,
) -> None:
    """
    Multi-strike adaptive sniper grid after verified short entry.

    Dispatches trap legs A/B/C (GTC), passive +$0.01 marination, 14:00 exp-day chase.
    """
    dry_run = not settings.is_live
    step = round(settings.sniper_marinate_step, 2)
    chase_sec = settings.sniper_chase_seconds

    traps: list[dict[str, Any]] = []
    trap_tasks = [
        _submit_sniper_trap_leg(
            settings,
            underlying=underlying,
            expiration=expiration,
            short_strike=short_strike,
            quantity=quantity,
            leg=leg,
            hedge_offset=offset,
            start_limit=start_limit,
            option_type=option_type,
            dry_run=dry_run,
        )
        for leg, offset, start_limit in SNIPER_TRAP_SPECS
    ]
    trap_results = await asyncio.gather(*trap_tasks)
    traps = [t for t in trap_results if t]

    if not traps:
        record_activity("sniper-grid", "failed", "No trap legs submitted", ticker=underlying)
        return

    record_protective_hedge_telemetry(
        traps,
        underlying=underlying,
        expiration=expiration,
        short_strike=short_strike,
        signal_id=signal_id,
    )
    record_activity(
        "sniper-grid",
        "started",
        f"Net deployed {len(traps)} traps short={short_strike} exp={expiration}",
        ticker=underlying,
        extra={"traps": [t["leg"] for t in traps], "short_strike": short_strike, "expiration": expiration},
    )
    logger.info("Sniper grid started %s — %s trap legs", underlying, len(traps))

    last_marinade = time.monotonic()
    force_phase_active = False

    while True:
        if not await alpaca_market_is_open(settings):
            record_activity("sniper-grid", "market_close", "Grid paused — market closed", ticker=underlying)
            return

        open_traps: list[dict[str, Any]] = []
        filled_traps: list[dict[str, Any]] = []
        for trap in traps:
            still_open, _order = await _trap_order_open(settings, trap)
            if trap.get("filled"):
                filled_traps.append(trap)
            elif still_open:
                open_traps.append(trap)

        if filled_traps:
            await _cancel_open_traps(
                settings,
                traps,
                skip_legs={t["leg"] for t in filled_traps},
            )
            ft = filled_traps[0]
            record_activity(
                "sniper-grid",
                "filled",
                f"Trap {ft['leg']} filled {ft['symbol']} @ {ft.get('filled_avg_price')}",
                ticker=underlying,
                extra=ft,
            )
            await notify(
                settings,
                "Sniper Grid Filled",
                f"Trap {ft['leg']} {ft['symbol']} @ ${ft.get('filled_avg_price')}",
                "SUCCESS",
            )
            return

        if not open_traps:
            record_activity("sniper-grid", "stopped", "All traps terminal — none filled", ticker=underlying)
            return

        now = now_exchange()
        if is_force_fill_phase(expiration, now):
            if not force_phase_active:
                await _cancel_open_traps(settings, traps, skip_legs={"A"})
                force_phase_active = True
                record_activity(
                    "sniper-grid",
                    "force_chase",
                    "14:00 exp-day — canceled B/C, chasing trap A",
                    ticker=underlying,
                )

            open_traps = []
            for trap in traps:
                still_open, _ = await _trap_order_open(settings, trap)
                if trap.get("filled"):
                    filled_traps = [trap]
                    break
                if still_open and trap["leg"] == "A":
                    open_traps.append(trap)
            if filled_traps:
                await _cancel_open_traps(settings, traps, skip_legs={filled_traps[0]["leg"]})
                ft = filled_traps[0]
                record_activity(
                    "sniper-grid",
                    "filled",
                    f"Trap {ft['leg']} filled {ft['symbol']} @ {ft.get('filled_avg_price')}",
                    ticker=underlying,
                    extra=ft,
                )
                await notify(
                    settings,
                    "Sniper Grid Filled",
                    f"Trap {ft['leg']} {ft['symbol']} @ ${ft.get('filled_avg_price')}",
                    "SUCCESS",
                )
                return

            leg_a = open_traps[0] if open_traps else None
            if leg_a is None:
                record_activity("sniper-grid", "stopped", "Force phase — trap A not open", ticker=underlying)
                return

            chase_limit = await fetch_hedge_chase_limit(
                settings,
                leg_a["symbol"],
                float(leg_a.get("limit_price") or 0.08),
            )
            await replace_alpaca_order_limit_price(settings, leg_a["order_id"], chase_limit)
            leg_a["limit_price"] = chase_limit
            await asyncio.sleep(chase_sec)
            continue

        interval = passive_interval_seconds(now)
        if time.monotonic() - last_marinade >= interval:
            for trap in open_traps:
                new_limit = round(float(trap.get("limit_price") or 0.0) + step, 2)
                if await replace_alpaca_order_limit_price(settings, trap["order_id"], new_limit):
                    trap["limit_price"] = new_limit
            last_marinade = time.monotonic()
            logger.info(
                "Sniper grid marinated %s open traps +$%s (interval %.0fs)",
                len(open_traps),
                step,
                interval,
            )

        sleep_for = min(30.0, max(1.0, interval - (time.monotonic() - last_marinade)))
        await asyncio.sleep(sleep_for)


def spawn_sniper_grid_after_short_fill(
    settings: Settings,
    *,
    underlying: str,
    expiration: str,
    short_strike: float,
    quantity: int,
    option_type: str = "put",
    signal_id: str | None = None,
) -> None:
    """Spawn adaptive sniper grid after verified short entry (non-blocking)."""
    if not settings.stx_sniper_grid_enabled:
        return
    asyncio.create_task(
        run_sniper_grid_loop(
            settings,
            underlying=underlying,
            expiration=expiration,
            short_strike=short_strike,
            quantity=quantity,
            option_type=option_type,
            signal_id=signal_id,
        )
    )
    logger.info(
        "Sniper grid spawned %s short %.2f exp %s",
        underlying,
        short_strike,
        expiration,
    )


def spawn_sniper_grid_after_entry(
    settings: Settings,
    signal: StxCloseSignal,
    *,
    expiration: str,
    short_strike: float,
) -> None:
    """STX webhook lane — respects per-signal disable flags."""
    if signal.enable_sniper_grid is False or signal.enable_put_walker is False:
        return
    spawn_sniper_grid_after_short_fill(
        settings,
        underlying=signal.underlying,
        expiration=expiration,
        short_strike=short_strike,
        quantity=signal.quantity,
        option_type=signal.option_type,
    )


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
            send_email=False,
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


def _chase_semaphore_for(settings: Settings) -> asyncio.Semaphore:
    global _chase_semaphore
    limit = max(1, int(settings.max_concurrent_chase_tasks))
    if _chase_semaphore is None:
        _chase_semaphore = asyncio.Semaphore(limit)
    return _chase_semaphore


def _burst_response_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Keep JSON small on Render free tier (avoid huge attempts[] in memory)."""
    attempts = result.get("attempts")
    if not isinstance(attempts, list):
        return result
    out = {k: v for k, v in result.items() if k != "attempts"}
    out["attempts_total"] = len(attempts)
    out["attempts_sample"] = attempts[-BURST_ATTEMPTS_RESPONSE_CAP:]
    out["attempts_omitted"] = max(0, len(attempts) - BURST_ATTEMPTS_RESPONSE_CAP)
    return out


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

    sem = _chase_semaphore_for(settings)
    try:
        await asyncio.wait_for(sem.acquire(), timeout=0.5)
    except asyncio.TimeoutError:
        logger.warning(
            "Chase slot busy (max=%s) — skipping background chase for %s",
            settings.max_concurrent_chase_tasks,
            entry_order_id[:8],
        )
        await notify(
            settings,
            "Entry Chase Deferred",
            f"Order {entry_order_id[:8]}… queued while server busy — check Alpaca Orders",
            "WARNING",
            send_email=False,
        )
        return

    try:
        await _alpaca_place_exits_after_fill_locked(
            settings, spread, entry_order_id, initial_credit=initial_credit
        )
    finally:
        sem.release()


async def _alpaca_place_exits_after_fill_locked(
    settings: Settings,
    spread: SpreadPackage,
    entry_order_id: str,
    *,
    initial_credit: float | None = None,
) -> None:
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
    credit = float(spread.metadata["limit_credit"])
    await notify(
        settings,
        "Entry Filled",
        f"Order {entry_order_id} filled at ${credit:.2f} credit — placing GTC TP/SL",
        "SUCCESS",
    )
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


def webhook_auth_json_response(
    auth_err: str,
    *,
    dry_run: bool,
    kind: Literal["entry", "warning"] = "entry",
) -> JSONResponse:
    """401 for bad secret (TradingView marks alert failed — correct for misconfig)."""
    if kind == "warning":
        content: dict[str, Any] = {
            "danger_zone": False,
            "action_taken": "auth_failed",
            "risk_warning": auth_err,
            "notifications": {},
        }
    else:
        content = {
            "success": False,
            "message": auth_err,
            "dry_run": dry_run,
            "notifications": {},
        }
    return JSONResponse(status_code=401, content=content)


async def submit_entry_sync_chase(
    settings: Settings,
    signal: TradingViewSignal,
    *,
    skip_exits: bool = True,
) -> dict[str, Any]:
    """
    Paper burst helper: submit entry, chase fill synchronously, cancel if stale.
    Returns attempt dict with order_id, status, filled.
    """
    spread, entry, entry_credit = await submit_entry_from_signal(settings, signal)
    if not entry.success:
        return {
            "filled": False,
            "order_id": None,
            "status": "rejected",
            "message": entry.message,
            "limit_credit": spread.metadata.get("limit_credit"),
        }

    meta = entry.broker_response or entry.payload or {}
    if spread.metadata.get("paper_spread_legs") or meta.get("paper_spread_legs"):
        return {
            "filled": True,
            "order_id": meta.get("short_order_id") or meta.get("order_id"),
            "status": "filled",
            "message": entry.message,
            "limit_credit": meta.get("net_credit_estimate") or spread.metadata.get("limit_credit"),
        }

    order_id = str(meta.get("id") or "")
    if not order_id:
        return {
            "filled": False,
            "order_id": None,
            "status": "no_order_id",
            "message": entry.message,
            "limit_credit": spread.metadata.get("limit_credit"),
        }

    start_credit = entry_credit if entry_credit is not None else float(spread.metadata["limit_credit"])
    filled_order = await wait_and_chase_alpaca_entry_fill(settings, order_id, start_credit)
    if filled_order:
        if not skip_exits and (settings.auto_take_profit or settings.auto_stop_loss):
            credit = resolve_entry_credit(spread, filled_order)
            spread_filled = spread_with_credit(spread, credit)
            asyncio.create_task(
                _place_exits_after_known_fill(settings, spread_filled, order_id, credit),
            )
        return {
            "filled": True,
            "order_id": order_id,
            "status": "filled",
            "message": "filled",
            "limit_credit": resolve_entry_credit(spread, filled_order),
        }

    await cancel_alpaca_order(settings, order_id)
    final = await fetch_alpaca_order(settings, order_id)
    status = str(final.get("status", "unfilled"))
    return {
        "filled": False,
        "order_id": order_id,
        "status": status,
        "message": f"not filled — canceled ({status})",
        "limit_credit": spread.metadata.get("limit_credit"),
    }


async def _place_exits_after_known_fill(
    settings: Settings,
    spread: SpreadPackage,
    entry_order_id: str,
    credit: float,
) -> None:
    """Submit GTC TP/SL when entry is already filled (burst optional exits)."""
    if settings.auto_take_profit:
        tp_payload = build_take_profit_payload(spread, settings.take_profit_pct, settings)
        tp = await submit_order(settings, tp_payload, dry_run=not settings.is_live)
        if tp.success:
            meta = tp_payload.get("_meta", {})
            await notify(
                settings,
                "GTC Take-Profit Resting",
                f"{spread.metadata.get('underlying', 'SPY')} close at ${meta.get('close_debit')} debit",
                "SUCCESS",
            )
    if settings.auto_stop_loss:
        sl_payload = build_stop_loss_payload(spread, settings.stop_loss_multiplier, settings)
        sl = await submit_order(settings, sl_payload, dry_run=not settings.is_live)
        if sl.success:
            meta = sl_payload.get("_meta", {})
            await notify(
                settings,
                "GTC Stop-Loss Resting",
                f"{spread.metadata.get('underlying', 'SPY')} close at ${meta.get('close_debit')} debit",
                "CRITICAL",
            )


async def run_burst_attempts(
    settings: Settings,
    signal: TradingViewSignal,
    count: int,
    *,
    interval_sec: float = 0.0,
    skip_exits: bool = True,
) -> dict[str, Any]:
    """Sequential paper burst: min credit, sync chase, cancel stale before next."""
    global _burst_in_progress
    if _burst_in_progress:
        return {
            "success": False,
            "burst_count": 0,
            "filled_count": 0,
            "message": "Burst already running on this instance — wait and retry",
            "attempts_total": 0,
            "attempts_sample": [],
        }
    cap = max(1, int(settings.burst_max_count))
    count = max(1, min(int(count), cap))
    attempts: list[dict[str, Any]] = []
    _burst_in_progress = True
    try:
        for i in range(count):
            if i > 0 and interval_sec > 0:
                await asyncio.sleep(interval_sec)
            attempt = await submit_entry_sync_chase(settings, signal, skip_exits=skip_exits)
            attempt["attempt"] = i + 1
            attempts.append(attempt)
            logger.info(
                "Burst %s/%s order=%s filled=%s status=%s",
                i + 1,
                count,
                (attempt.get("order_id") or "")[:8],
                attempt.get("filled"),
                attempt.get("status"),
            )
        filled_count = sum(1 for a in attempts if a.get("filled"))
        await notify(
            settings,
            "Burst Complete",
            f"Paper burst finished: {filled_count}/{count} filled",
            "SUCCESS" if filled_count > 0 else "WARNING",
        )
        return {
            "success": filled_count > 0,
            "burst_count": count,
            "filled_count": filled_count,
            "attempts": attempts,
        }
    finally:
        _burst_in_progress = False


async def submit_short_put_batches(
    settings: Settings,
    spread: SpreadPackage,
    signal: TradingViewSignal,
    *,
    dry_run: bool,
) -> OrderResult:
    """Split large short-put entries into smaller batches (low-liquidity tickers)."""
    total_qty = int(spread.qty)
    batch_size = int(spread.metadata.get("entry_batch_size") or 0)
    if batch_size <= 0 or batch_size >= total_qty:
        entry_payload = build_alpaca_entry_payload(spread) if settings.use_alpaca else build_entry_payload(spread)
        return await submit_order(settings, entry_payload, dry_run=dry_run)

    batches = split_batches(total_qty, batch_size)
    last_result = OrderResult(success=False, message="No batches submitted", dry_run=dry_run)
    filled = 0
    for i, qty in enumerate(batches, start=1):
        batch_spread = SpreadPackage(
            qty=str(qty),
            legs=spread.legs,
            metadata={**spread.metadata, "batch_index": i, "batch_total": len(batches)},
        )
        payload = build_alpaca_entry_payload(batch_spread) if settings.use_alpaca else build_entry_payload(batch_spread)
        result = await submit_order(settings, payload, dry_run=dry_run)
        last_result = result
        if result.success:
            filled += qty
        if i < len(batches):
            await asyncio.sleep(0.5)
    return OrderResult(
        success=filled > 0,
        message=f"Short put batches: {filled}/{total_qty} contracts submitted ({len(batches)} orders)",
        dry_run=dry_run,
        payload={"filled_qty": filled, "total_qty": total_qty, "batches": len(batches)},
        broker_response=last_result.broker_response,
    )


async def submit_entry_from_signal(
    settings: Settings,
    signal: TradingViewSignal,
) -> tuple[SpreadPackage, OrderResult, float | None]:
    """
    Build spread, resolve credit, submit entry. Returns (spread, result, entry_credit).
    entry_credit is limit credit used for Alpaca chase scheduling.
    """
    dry_run = not settings.is_live
    spread = build_order_from_signal(signal, settings)
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

    order_kind = "Alpaca single-leg" if spread.metadata.get("single_leg") else "Alpaca mleg"
    logger.info("Submitting %s entry order", order_kind if settings.use_alpaca else "Tastytrade")

    if settings.use_alpaca and settings.auto_cancel_conflicting_orders and settings.alpaca_configured:
        leg_syms = {leg.symbol for leg in spread.legs}
        sym_canceled = await cancel_alpaca_open_orders_for_symbols(settings, leg_syms)
        mleg_canceled = 0 if spread.metadata.get("single_leg") else await cancel_open_mleg_orders(settings)
        total = sym_canceled + mleg_canceled
        if total:
            logger.info(
                "Pre-entry cleanup: canceled %s open order(s) (%s symbol, %s mleg)",
                total,
                sym_canceled,
                mleg_canceled,
            )

    if signal.is_short_put and spread.metadata.get("entry_batch_size"):
        entry = await submit_short_put_batches(settings, spread, signal, dry_run=dry_run)
    elif should_use_paper_spread_legs(settings, spread):
        logger.info("Alpaca paper: using leg-by-leg spread entry (no mleg)")
        ok, msg, meta = await submit_paper_spread_entry(settings, spread, dry_run=dry_run)
        spread.metadata["paper_spread_legs"] = True
        if meta.get("net_credit_estimate") is not None:
            spread.metadata["limit_credit"] = meta["net_credit_estimate"]
        entry = OrderResult(
            success=ok,
            message=msg,
            dry_run=dry_run,
            payload=meta,
            broker_response=meta,
        )
    elif settings.is_alpaca_paper and len(spread.legs) >= 2 and not spread.metadata.get("single_leg"):
        logger.error("Alpaca paper 2-leg spread reached mleg path — blocked")
        entry = OrderResult(
            success=False,
            message="Alpaca paper requires leg-by-leg spread entry",
            dry_run=dry_run,
        )
    else:
        entry_payload = build_alpaca_entry_payload(spread) if settings.use_alpaca else build_entry_payload(spread)
        entry = await submit_order(settings, entry_payload, dry_run=dry_run)
    if (
        settings.use_alpaca
        and settings.auto_cancel_conflicting_orders
        and not entry.success
        and "(403)" in entry.message
    ):
        mleg_canceled = await cancel_open_mleg_orders(settings)
        logger.warning("Entry rejected 403 — canceled %s open mleg order(s), retrying once", mleg_canceled)
        if signal.is_short_put and spread.metadata.get("entry_batch_size"):
            entry = await submit_short_put_batches(settings, spread, signal, dry_run=dry_run)
        elif should_use_paper_spread_legs(settings, spread):
            ok, msg, meta = await submit_paper_spread_entry(settings, spread, dry_run=dry_run)
            entry = OrderResult(
                success=ok,
                message=msg,
                dry_run=dry_run,
                payload=meta,
                broker_response=meta,
            )
        else:
            retry_payload = (
                build_alpaca_entry_payload(spread) if settings.use_alpaca else build_entry_payload(spread)
            )
            entry = await submit_order(settings, retry_payload, dry_run=dry_run)

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


async def place_paper_spread_exits(settings: Settings, spread: SpreadPackage) -> None:
    """Paper spreads: legs are open; notify — warning alert handles emergency close."""
    ticker = str(spread.metadata.get("underlying", "SPY"))
    credit = spread.metadata.get("limit_credit", "?")
    await notify(
        settings,
        "Paper Spread Position Open",
        f"{ticker} put credit spread legs filled (~${credit} net). "
        f"Warning alert auto-closes on danger. Check Alpaca Positions.",
        "SUCCESS",
    )


async def process_entry_alert(settings: Settings, signal: TradingViewSignal) -> None:
    """Run Alpaca/Tastytrade entry + exit scheduling (TradingView gets an instant 200 first)."""
    dry_run = not settings.is_live
    try:
        spread, entry, entry_credit = await submit_entry_from_signal(settings, signal)
        expiration = spread.metadata["expiration"]

        if entry.success:
            outcome = "filled" if spread.metadata.get("paper_spread_legs") else "submitted"
            record_activity(
                "entry",
                outcome,
                f"{'Legs filled' if outcome == 'filled' else 'Order submitted (mleg)'} exp={expiration} credit=${spread.metadata.get('limit_credit')}",
                ticker=signal.ticker,
                extra={"strategy": spread.metadata.get("strategy"), "paper_spread_legs": spread.metadata.get("paper_spread_legs")},
            )
            await notify(
                settings,
                "Entry Submitted",
                f"{signal.ticker} {spread.metadata['strategy']} exp={expiration} credit=${spread.metadata['limit_credit']}",
                "SUCCESS" if settings.is_live else "INFO",
            )
            # Sniper grid — Crush-It single short puts after entry.success (SPY spreads unchanged).
            if (
                spread.metadata.get("single_leg")
                and spread.metadata.get("strategy") == "short_put"
                and signal.ticker.upper() != "SPY"
            ):
                spawn_sniper_grid_after_short_fill(
                    settings,
                    underlying=signal.ticker,
                    expiration=str(spread.metadata["expiration"]),
                    short_strike=float(spread.metadata["short_strike"]),
                    quantity=int(signal.quantity),
                )
        else:
            record_activity("entry", "rejected", entry.message, ticker=signal.ticker)
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

        elif spread.metadata.get("paper_spread_legs"):
            if settings.auto_take_profit or settings.auto_stop_loss:
                asyncio.create_task(place_paper_spread_exits(settings, spread))
        elif not spread.metadata.get("single_leg"):
            schedule_alpaca_exits_after_entry(settings, spread, entry, entry_credit)
    except Exception as exc:
        logger.exception("Background entry processing failed: %s", exc)
        record_activity("entry", "failed", str(exc), ticker=signal.ticker)
        await notify(settings, "Entry Failed", str(exc), "CRITICAL")


async def process_conservative_close_put(settings: Settings, close_signal: ClosePutSignal) -> None:
    """Buy back short puts in batches with limit = bid + premium (conservative close)."""
    dry_run = not settings.is_live
    if not settings.use_alpaca or not settings.alpaca_configured:
        await notify(settings, "Close Put Skipped", "Alpaca not configured", "WARNING")
        return

    expiration = None
    if close_signal.expiration:
        expiration = resolve_dte_expiration(close_signal.expiration, close_signal.dte_filter)
    elif close_signal.dte_filter:
        expiration = resolve_dte_expiration("0dte", close_signal.dte_filter)

    positions = await fetch_alpaca_positions(settings)
    matches = find_short_puts_in_positions(
        positions,
        close_signal.ticker,
        short_strike=close_signal.short_strike,
        expiration=expiration,
    )
    if not matches:
        await notify(
            settings,
            "Close Put — No Position",
            f"No short put found for {close_signal.ticker}"
            + (f" strike ${close_signal.short_strike}" if close_signal.short_strike else ""),
            "WARNING",
        )
        return

    pos = matches[0]
    meta = pos["_meta"]
    open_qty = int(pos["_qty"])
    close_qty = min(close_signal.quantity or open_qty, open_qty)
    batches = split_batches(close_qty, close_signal.batch_size)
    sym = str(pos["symbol"])
    put_spread = SpreadPackage(
        qty="1",
        legs=[SpreadLeg(symbol=sym, side="sell", position_intent="sell_to_open")],
        metadata={
            "underlying": close_signal.ticker,
            "strategy": "short_put",
            "short_strike": meta.get("strike"),
            "expiration": meta.get("expiration"),
            "single_leg": True,
        },
    )

    submitted = 0
    last_bid = 0.0
    for i, batch_qty in enumerate(batches, start=1):
        quotes = await fetch_option_snapshot_quotes(settings, [sym])
        bid = quotes.get(sym.upper(), {}).get("bid", 0.0)
        last_bid = bid
        limit = conservative_close_limit(bid, close_signal.bid_premium)
        batch_pkg = SpreadPackage(
            qty=str(batch_qty),
            legs=put_spread.legs,
            metadata={**put_spread.metadata, "batch_index": i, "close_limit": limit},
        )
        payload = build_alpaca_single_leg_payload(batch_pkg, limit_price=limit, opening=False, time_in_force="day")
        result = await submit_alpaca_order(settings, payload, dry_run=dry_run)
        if not result.success:
            for step in range(close_signal.max_chase_steps):
                limit = round(limit + close_signal.chase_step, 2)
                payload["limit_price"] = f"{limit:.2f}"
                result = await submit_alpaca_order(settings, payload, dry_run=dry_run)
                if result.success:
                    break
                await asyncio.sleep(0.3)
        if result.success:
            submitted += batch_qty
        if i < len(batches):
            await asyncio.sleep(0.5)

    await notify(
        settings,
        "Conservative Close Complete",
        f"{close_signal.ticker} buy-to-close: {submitted}/{close_qty} contracts "
        f"in {len(batches)} batch(es) @ ~${conservative_close_limit(last_bid, close_signal.bid_premium):.2f}",
        "SUCCESS" if submitted > 0 else "WARNING",
    )


async def process_warning_alert(
    settings: Settings,
    warning: WarningSignal,
    msg: str,
    distance: float,
    survival: float,
    protocol_notes: list[str],
) -> None:
    """Run warning notify + optional Alpaca close (TradingView gets instant 200 first)."""
    dry_run = not settings.is_live
    notifications: dict = {}
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

    if warning.override_auto_close:
        await notify(
            settings,
            "Warning — Override Active",
            f"{msg}\nAuto-close SKIPPED (overrideAutoClose=true). Survival odds: {survival * 100:.1f}%",
            "WARNING",
        )
        return

    should_close = warning.force_auto_close or settings.auto_close_on_warning
    if not should_close:
        return

    if not settings.use_alpaca or not settings.alpaca_configured:
        return

    positions = await fetch_alpaca_positions(settings)
    spreads = find_put_credit_spreads_in_positions(
        positions,
        warning.ticker,
        short_strike=warning.short_strike,
        long_strike=warning.long_strike,
    )

    if not spreads:
        record_activity(
            "warning",
            "no_position",
            f"No open spread at short ${warning.short_strike:.2f}",
            ticker=warning.ticker,
        )
        await notify(
            settings,
            "Warning — No Spread Found",
            f"{msg}\nNo open put credit spread matched short strike ${warning.short_strike:.2f}.",
            "WARNING",
        )
        return

    spread = spreads[0]
    close_debit = resolve_warning_close_debit(spread, settings, override_debit=warning.close_debit)
    leg_syms = {leg.symbol for leg in spread.legs}

    if settings.warning_cancel_resting_exits and not dry_run:
        n = await cancel_alpaca_open_orders_for_symbols(settings, leg_syms)
        if n:
            protocol_notes.append(
                f"Canceled {n} resting order(s) on spread legs before emergency close"
            )

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
        f"{msg}\nClosing spread at ${close_debit:.2f} debit. Survival odds were {survival * 100:.1f}%.",
        "CRITICAL",
    )

    if settings.is_alpaca_paper:
        ok, msg = await close_paper_spread_legs(settings, spread, dry_run=dry_run)
        record_activity(
            "warning",
            "closed" if ok else "failed",
            msg[:200],
            ticker=warning.ticker,
        )
        await notify(
            settings,
            "Warning — Spread Closed" if ok else "Warning — Close Failed",
            f"{msg}\nSurvival odds were {survival * 100:.1f}%.",
            "SUCCESS" if ok else "CRITICAL",
        )
        return

    close_order = await submit_alpaca_order(settings, close_payload, dry_run=dry_run)
    if not close_order.success:
        await notify(settings, "Warning — Auto-Close Failed", close_order.message, "CRITICAL")


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
    version="5.5.28",
    description="TradingView → Alpaca Paper credit spreads + short puts + conservative close",
)


@app.on_event("startup")
async def startup_log() -> None:
    global _app_started_mono
    _app_started_mono = time.monotonic()
    log_timezone_audit()
    s = get_settings()
    broker_label = "Alpaca Paper" if s.use_alpaca else "Tastytrade Cert"
    logger.info(
        "spy-options-bridge v5.5.12 started | broker=%s (%s) | configured=%s | mode=%s | auto_tp=%s auto_sl=%s chase=%s paper_force=%s auto_cancel=%s",
        s.broker,
        broker_label,
        s.configured,
        s.execution_mode,
        s.auto_take_profit,
        s.auto_stop_loss,
        s.auto_chase_entry_fill,
        s.paper_force_min_fill and s.is_alpaca_paper,
        s.auto_cancel_conflicting_orders,
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


# ── STX close webhook (Stage 2 — routes to stx_watcher / stx_kill logic) ─────


def _stx_env_from_settings(settings: Settings) -> dict[str, str]:
    return {
        "APCA_API_KEY_ID": settings.alpaca_key,
        "APCA_API_SECRET_KEY": settings.alpaca_secret,
        "APCA_API_BASE_URL": settings.apca_api_base_url.rstrip("/"),
    }


def _resolve_stx_expiration(signal: StxCloseSignal) -> str:
    """Auto-roll weekly/0dte like SPY — no manual YYYY-MM-DD in TV unless you want a fixed contract."""
    raw = (signal.expiration or signal.dte_filter or "weekly").strip()
    dte = signal.dte_filter
    if dte or raw.lower() in {
        "0dte", "today", "weekly", "week", "+0 week", "0dte_weekly",
        "+1dte", "tomorrow", "+2dte",
    }:
        return resolve_dte_expiration(raw, dte or raw)
    return resolve_dte_expiration(raw, dte)


def _stx_config_from_open_position(
    settings: Settings,
    signal: StxCloseSignal,
) -> StxConfig | None:
    """Close uses the put you actually hold — not a stale JSON date."""
    import httpx

    base = settings.apca_api_base_url.rstrip("/")
    headers = _alpaca_headers(settings)
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.get(f"{base}/v2/positions", headers=headers)
        if not r.is_success:
            return None
        for pos in r.json():
            sym = str(pos.get("symbol", ""))
            parsed = parse_occ_symbol(sym)
            if not parsed:
                continue
            if parsed["underlying"] != signal.underlying.upper():
                continue
            if parsed["option_type"] != signal.option_type:
                continue
            qty = float(pos.get("qty") or 0)
            if qty >= 0:
                continue
            return StxConfig(
                underlying=signal.underlying,
                expiration=parsed["expiration"],
                option_type=parsed["option_type"],
                strike=float(parsed["strike"]),
                poll_seconds=signal.poll_seconds,
                prev_close_iv=signal.prev_close_iv,
            )
    except Exception as exc:
        logger.warning("STX position lookup failed: %s", exc)
    return None


def _stx_config_from_signal(signal: StxCloseSignal, state: dict[str, Any] | None) -> StxConfig:
    if signal.strike is not None:
        return StxConfig(
            underlying=signal.underlying,
            expiration=_resolve_stx_expiration(signal),
            option_type=signal.option_type,
            strike=float(signal.strike),
            poll_seconds=signal.poll_seconds,
            prev_close_iv=signal.prev_close_iv,
        )
    if state and isinstance(state.get("config"), dict):
        c = state["config"]
        return StxConfig(
            underlying=str(c.get("underlying", signal.underlying)),
            expiration=str(c["expiration"]),
            option_type=str(c.get("option_type", "put")),
            strike=float(c["strike"]),
            poll_seconds=int(c.get("poll_seconds", signal.poll_seconds)),
            prev_close_iv=c.get("prev_close_iv"),
        )
    raise ValueError("expiration and strike required when no watcher state file exists")


def _stx_short_leg_qty(legs: list[dict[str, Any]], cfg: StxConfig) -> int:
    for leg in legs:
        if str(leg.get("symbol", "")).upper() == cfg.short_symbol:
            return abs(int(float(leg.get("qty", "0"))))
    return 0


def _stx_fresh_snapshot(
    client: httpx.Client,
    env: dict[str, str],
    cfg: StxConfig,
    *,
    prior_move_pct: float | None,
) -> tuple[MarketSnapshot, list[dict[str, Any]], bool]:
    now = now_exchange()
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
        underlying_move_pct=prior_move_pct,
        bid=bid,
        ask=ask,
        midpoint=mid,
        spread=spread,
        iv=iv,
        iv_delta_points=iv_delta,
        position_open=len(legs) > 0,
    )
    return snap, legs, fetch_ok


def _stx_poll_order_filled(
    client: httpx.Client,
    base: str,
    h: dict[str, str],
    order_id: str,
    *,
    timeout_sec: float = 22.0,
    poll_sec: float = 1.5,
) -> bool:
    import time

    deadline = time.time() + timeout_sec
    terminal = {"canceled", "expired", "rejected", "failed", "done_for_day"}
    while time.time() < deadline:
        r = client.get(f"{base}/v2/orders/{order_id}", headers=h, timeout=20)
        if r.is_success:
            o = r.json()
            status = str(o.get("status", "")).lower()
            if status == "filled" or float(o.get("filled_qty") or 0) > 0:
                return True
            if status in terminal:
                return False
        time.sleep(poll_sec)
    return False


def _stx_execute_recommendation(
    client: httpx.Client,
    env: dict[str, str],
    cfg: StxConfig,
    legs: list[dict[str, Any]],
    rec: Recommendation,
    *,
    dry_run: bool,
) -> list[dict[str, Any]]:
    base = alpaca_base(env)
    h = alpaca_headers(env)
    results: list[dict[str, Any]] = []

    if rec.action == "market":
        for leg in legs:
            sym = str(leg.get("symbol", ""))
            if dry_run:
                results.append({"symbol": sym, "action": "market_close", "dry_run": True})
                continue
            r = client.delete(f"{base}/v2/positions/{sym}", headers=h, timeout=30)
            results.append(
                {
                    "symbol": sym,
                    "action": "market_close",
                    "http_status": r.status_code,
                    "ok": r.is_success,
                }
            )
    elif rec.action == "limit" and rec.limit_price is not None:
        qty = _stx_short_leg_qty(legs, cfg)
        if qty <= 0:
            raise ValueError("short leg not found in open positions")
        order = {
            "symbol": cfg.short_symbol,
            "qty": str(qty),
            "side": "buy",
            "type": "limit",
            "time_in_force": "day",
            "limit_price": str(rec.limit_price),
            "position_intent": "buy_to_close",
        }
        if dry_run:
            results.append({"symbol": cfg.short_symbol, "action": "limit_close", "order": order, "dry_run": True})
        else:
            r = client.post(f"{base}/v2/orders", headers=h, json=order, timeout=30)
            ok = r.is_success
            oid = ""
            if ok:
                try:
                    oid = str(r.json().get("id", ""))
                except Exception:
                    oid = ""
            filled = bool(oid) and _stx_poll_order_filled(client, base, h, oid)
            if ok and not filled and oid:
                client.delete(f"{base}/v2/orders/{oid}", headers=h, timeout=20)
                mr = client.delete(f"{base}/v2/positions/{cfg.short_symbol}", headers=h, timeout=30)
                results.append(
                    {
                        "symbol": cfg.short_symbol,
                        "action": "limit_close_then_market",
                        "http_status": mr.status_code,
                        "ok": mr.is_success,
                        "filled": mr.is_success,
                    }
                )
            else:
                results.append(
                    {
                        "symbol": cfg.short_symbol,
                        "action": "limit_close",
                        "http_status": r.status_code,
                        "ok": ok and filled,
                        "filled": filled,
                        "order_id": oid[:8] if oid else None,
                    }
                )
    return results


def run_stx_close_evaluate(settings: Settings, signal: StxCloseSignal) -> dict[str, Any]:
    if not _STX_MODULES_OK:
        raise RuntimeError("STX strategy modules not available on this server")

    env = _stx_env_from_settings(settings)
    if not is_paper(env):
        raise ValueError(f"refusing non-paper Alpaca base: {alpaca_base(env)}")
    if not (env.get("APCA_API_KEY_ID") or env.get("ALPACA_API_SECRET_KEY")):
        raise ValueError("Alpaca credentials missing — set APCA_API_KEY_ID and APCA_API_SECRET_KEY")

    state = read_state()
    cfg = _stx_config_from_signal(signal, state)
    prior_move_pct: float | None = None
    if signal.mode != "poll" and state and isinstance(state.get("snapshot"), dict):
        raw = state["snapshot"].get("underlying_move_pct")
        prior_move_pct = float(raw) if raw is not None else None

    with httpx.Client() as client:
        snap, legs, fetch_ok = _stx_fresh_snapshot(client, env, cfg, prior_move_pct=prior_move_pct)
        if not fetch_ok:
            raise RuntimeError("could not fetch Alpaca positions — aborting for safety")
        rec = build_recommendation(snap, cfg, now_et=now_exchange())
        state_written = False
        if signal.mode == "poll":
            write_state(cfg, snap, rec)
            state_written = True

        watcher_rec = state.get("recommendation") if state else None
        return {
            "success": True,
            "action_taken": signal.mode,
            "symbol": cfg.short_symbol,
            "position_open": snap.position_open,
            "open_legs": [leg.get("symbol") for leg in legs],
            "snapshot": asdict(snap),
            "recommendation": asdict(rec),
            "watcher_state_written": state_written,
            "watcher_recommendation": watcher_rec,
            "dry_run": not settings.is_live,
        }


def run_stx_close_execute(settings: Settings, signal: StxCloseSignal) -> dict[str, Any]:
    eval_result = run_stx_close_evaluate(
        settings,
        StxCloseSignal(
            underlying=signal.underlying,
            expiration=signal.expiration,
            strike=signal.strike,
            option_type=signal.option_type,
            mode="evaluate",
            confirm_close=signal.confirm_close,
            prev_close_iv=signal.prev_close_iv,
            poll_seconds=signal.poll_seconds,
        ),
    )
    rec_raw = eval_result.get("recommendation") or {}
    action = str(rec_raw.get("action", "hold"))
    if not eval_result.get("position_open") or action == "hold":
        return {
            **eval_result,
            "action_taken": "no_close_needed",
            "orders": [],
            "message": "Position closed or HOLD — no orders sent",
        }

    env = _stx_env_from_settings(settings)
    state = read_state()
    cfg = _stx_config_from_open_position(settings, signal) or _stx_config_from_signal(signal, state)
    dry_run = not settings.is_live

    with httpx.Client() as client:
        snap, legs, fetch_ok = _stx_fresh_snapshot(
            client,
            env,
            cfg,
            prior_move_pct=(eval_result.get("snapshot") or {}).get("underlying_move_pct"),
        )
        if not fetch_ok:
            raise RuntimeError("could not fetch Alpaca positions for execute")
        rec = build_recommendation(snap, cfg, now_et=now_exchange())
        orders = _stx_execute_recommendation(client, env, cfg, legs, rec, dry_run=dry_run)

    return {
        **eval_result,
        "action_taken": "executed" if not dry_run else "dry_run_executed",
        "orders": orders,
        "message": "STX close orders submitted" if not dry_run else "STX close dry-run complete",
    }


async def process_stx_open_execute(settings: Settings, signal: StxCloseSignal) -> None:
    try:
        resolved = await resolve_stx_open_contract(settings, signal)
        expiration = resolved["expiration"]
        strike = float(resolved["strike"])
        contract_meta = resolved.get("meta") or {}
        exp_date = datetime.strptime(expiration[:10], "%Y-%m-%d").date()
        if exp_date < now_exchange().date():
            raise ValueError(f"resolved expiration {expiration} is in the past")
        dry_run = not settings.is_live
        ok, msg, meta = await open_crush_it_short_put(
            settings,
            underlying=signal.underlying,
            expiration=expiration,
            strike=strike,
            option_type=signal.option_type,
            quantity=signal.quantity,
            dry_run=dry_run,
        )
        if isinstance(meta, dict):
            meta = {**contract_meta, **meta}
        else:
            meta = contract_meta
        record_activity(
            "stx-close",
            "opened" if ok else "failed",
            msg[:200],
            ticker=signal.underlying,
            extra=meta,
        )
        if ok:
            await notify(settings, "Crush-It Open", msg, "SUCCESS")
            spawn_sniper_grid_after_entry(
                settings,
                signal,
                expiration=expiration,
                short_strike=strike,
            )
        else:
            await notify(settings, "Crush-It Open Failed", msg, "CRITICAL")
    except Exception as exc:
        logger.exception("STX open failed: %s", exc)
        record_activity("stx-close", "failed", str(exc)[:200], ticker=signal.underlying)


async def process_stx_close_execute(settings: Settings, signal: StxCloseSignal) -> None:
    try:
        result = await asyncio.to_thread(run_stx_close_execute, settings, signal)
        logger.info(
            "STX close execute complete symbol=%s action=%s orders=%s",
            result.get("symbol"),
            result.get("action_taken"),
            len(result.get("orders") or []),
        )
        record_activity(
            "stx-close",
            str(result.get("action_taken", "executed")),
            str(result.get("message", "STX close processed"))[:200],
            ticker=signal.underlying,
            extra={"symbol": result.get("symbol"), "orders": len(result.get("orders") or [])},
        )
    except Exception as exc:
        logger.exception("STX close execute failed: %s", exc)
        record_activity(
            "stx-close",
            "failed",
            str(exc)[:200],
            ticker=signal.underlying,
        )


@app.get("/health")
async def health() -> dict[str, Any]:
    s = get_settings()
    broker_name = "alpaca" if s.use_alpaca else s.broker
    preflight = (
        await alpaca_open_order_preflight_cached(s)
        if s.use_alpaca and s.alpaca_configured
        else {}
    )
    tv_pause_risk = build_tv_pause_risk(s, preflight)
    return {
        "status": "ok" if tv_pause_risk["level"] != "red" else "degraded",
        "version": "5.5.28",
        "spread_max_trades_per_day": str(s.spread_max_trades_per_day),
        "spread_daily_loss_limit": str(s.spread_daily_loss_limit),
        "burst_endpoint": "/exercise/burst",
        "auto_take_profit": str(s.auto_take_profit),
        "auto_stop_loss": str(s.auto_stop_loss),
        "broker": broker_name,
        "broker_label": "Alpaca Paper" if s.use_alpaca else "Tastytrade Cert",
        "mode": s.execution_mode,
        "configured": str(s.configured),
        "webhook_secret_configured": str(bool(s.webhook_secret)),
        "email_configured": s.email_configured,
        "email_enabled": s.email_enabled,
        "api": s.apca_api_base_url if s.use_alpaca else s.tastytrade_api_base_url,
        "dte_filter_default": s.default_dte_filter,
        "spread_mode_only": str(s.spread_mode_only),
        "default_strike_offset_short": str(s.default_strike_offset_short),
        "default_strike_offset_long": str(s.default_strike_offset_long),
        "default_limit_credit": str(s.default_limit_credit),
        "spread_min_credit": str(s.spread_min_credit),
        "auto_close_on_warning": str(s.auto_close_on_warning),
        "warning_close_multiplier": str(s.warning_close_multiplier),
        "default_fill_mode": s.default_fill_mode,
        "auto_chase_entry_fill": str(s.auto_chase_entry_fill),
        "entry_chase_wait_seconds": str(s.entry_chase_wait_seconds),
        "entry_chase_poll_seconds": str(s.entry_chase_poll_seconds),
        "entry_min_credit": str(s.entry_min_credit),
        "paper_force_min_fill": str(s.paper_force_min_fill and s.is_alpaca_paper),
        "auto_cancel_conflicting_orders": str(s.auto_cancel_conflicting_orders),
        "open_mleg_count": str(preflight.get("open_mleg_count", "")),
        "open_order_count": str(preflight.get("open_order_count", "")),
        "tv_pause_risk": tv_pause_risk,
        "deploy_file": "main.py (root — NOT app/main.py)",
    }


@app.get("/ping")
async def ping() -> dict[str, str]:
    """Lightweight keep-alive for cron pings (prevents Render free-tier cold starts)."""
    return {"status": "ok", "version": "5.5.28"}


@app.get("/activity")
async def activity_log() -> dict[str, Any]:
    """Today's webhook timeline — merges memory + logs/activity.jsonl on disk."""
    today = now_exchange().strftime("%Y-%m-%d")
    events: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(row: dict[str, Any]) -> None:
        ts = str(row.get("ts_et", ""))
        if not ts.startswith(today):
            return
        key = f"{ts}|{row.get('kind')}|{row.get('outcome')}|{row.get('message', '')[:80]}"
        if key in seen:
            return
        seen.add(key)
        events.append(row)

    for e in _activity_log:
        _add(e)
    if _ACTIVITY_LOG_PATH.is_file():
        try:
            for line in _ACTIVITY_LOG_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                _add(json.loads(line))
        except Exception as exc:
            logger.debug("activity file read skipped: %s", exc)
    events.sort(key=lambda r: str(r.get("ts_et", "")), reverse=True)
    return {
        "status": "ok",
        "version": "5.5.28",
        "today": today,
        "count": len(events),
        "note": "Memory + logs/activity.jsonl. Alpaca proof = Activities tab fills.",
        "events": events,
    }


def _burst_paper_guard(settings: Settings) -> str | None:
    if not settings.is_alpaca_paper:
        return "Burst mode is paper-only — set APCA_API_BASE_URL to paper-api.alpaca.markets"
    if not settings.configured:
        return "Alpaca credentials not configured"
    return None


def _parse_burst_count(
    payload: dict[str, Any],
    query_count: int | None,
    *,
    max_count: int = 10,
) -> int:
    cap = max(1, int(max_count))
    raw = payload.pop("burstCount", None) or payload.pop("burst_count", None)
    if raw is not None:
        try:
            return max(1, min(int(raw), cap))
        except (TypeError, ValueError):
            pass
    if query_count is not None:
        return max(1, min(int(query_count), cap))
    return 1


@app.post("/exercise/burst")
async def exercise_burst_endpoint(
    request: Request,
    count: int | None = None,
    interval: float = 0.0,
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
) -> JSONResponse:
    """
    Paper validation burst: N sequential exercise fills (min $0.05 credit, sync chase).
    Query: ?count=N&interval=2  or JSON burstCount + webhookSecret.
    Max BURST_MAX_COUNT per request (default 10 on Render); use burst_paper_fills.py for client-side pacing.
    """
    settings = get_settings()
    dry_run = not settings.is_live

    guard = _burst_paper_guard(settings)
    if guard:
        return JSONResponse(status_code=200, content={"success": False, "message": guard, "dry_run": dry_run})

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    if not isinstance(payload, dict):
        payload = {}

    provided, payload = extract_webhook_secret(payload, x_webhook_secret)
    auth_err = webhook_auth_error(provided, settings.webhook_secret)
    if auth_err:
        return webhook_auth_json_response(auth_err, dry_run=dry_run, kind="entry")

    burst_n = _parse_burst_count(payload, count, max_count=settings.burst_max_count)
    interval_sec = float(payload.pop("burstInterval", payload.pop("burst_interval", interval)) or interval)

    payload = dict(payload)
    payload["fillMode"] = "exercise"
    payload.setdefault("action", "PUT_CREDIT_SPREAD")
    skip_exits = bool(payload.pop("skipExits", payload.pop("skip_exits", True)))

    try:
        signal = coerce_signal(payload, settings)
        build_order_from_signal(signal, settings)
    except (ValidationError, ValueError) as exc:
        return JSONResponse(
            status_code=200,
            content={"success": False, "message": f"Bad payload: {exc}", "dry_run": dry_run},
        )

    try:
        result = await run_burst_attempts(
            settings,
            signal,
            burst_n,
            interval_sec=interval_sec,
            skip_exits=skip_exits,
        )
    except Exception as exc:
        logger.exception("Burst failed: %s", exc)
        return JSONResponse(
            status_code=200,
            content={"success": False, "message": str(exc), "dry_run": dry_run},
        )

    slim = _burst_response_payload(result)
    return JSONResponse(
        status_code=200,
        content={
            "success": slim["success"],
            "message": f"Burst complete: {slim['filled_count']}/{slim['burst_count']} filled",
            "dry_run": dry_run,
            **slim,
        },
    )


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
        return webhook_auth_json_response(auth_err, dry_run=dry_run, kind="entry")

    guard = _burst_paper_guard(settings)
    if guard:
        return JSONResponse(status_code=200, content={"success": False, "message": guard, "dry_run": dry_run})

    payload = dict(payload)
    payload["fillMode"] = "exercise"
    payload.setdefault("action", "PUT_CREDIT_SPREAD")

    burst_n = _parse_burst_count(payload, None, max_count=settings.burst_max_count)
    if burst_n > 1:
        try:
            signal = coerce_signal(payload, settings)
            build_order_from_signal(signal, settings)
        except (ValidationError, ValueError) as exc:
            return JSONResponse(
                status_code=200,
                content={"success": False, "message": f"Bad payload: {exc}", "dry_run": dry_run},
            )
        interval_sec = float(payload.get("burstInterval", payload.get("burst_interval", 0)) or 0)
        skip_exits = bool(payload.get("skipExits", payload.get("skip_exits", True)))
        try:
            result = await run_burst_attempts(
                settings,
                signal,
                burst_n,
                interval_sec=interval_sec,
                skip_exits=skip_exits,
            )
        except Exception as exc:
            logger.exception("Exercise burst failed: %s", exc)
            return JSONResponse(
                status_code=200,
                content={"success": False, "message": str(exc), "dry_run": dry_run},
            )
        slim = _burst_response_payload(result)
        return JSONResponse(
            status_code=200,
            content={
                "success": slim["success"],
                "message": f"Burst complete: {slim['filled_count']}/{slim['burst_count']} filled",
                "dry_run": dry_run,
                **slim,
            },
        )

    try:
        signal = coerce_signal(payload, settings)
        build_order_from_signal(signal, settings)
    except (ValidationError, ValueError) as exc:
        return JSONResponse(
            status_code=200,
            content={"success": False, "message": f"Bad payload: {exc}", "dry_run": dry_run},
        )

    try:
        attempt = await submit_entry_sync_chase(settings, signal, skip_exits=False)
    except Exception as exc:
        logger.exception("Exercise entry failed: %s", exc)
        return JSONResponse(
            status_code=200,
            content={"success": False, "message": str(exc), "dry_run": dry_run},
        )

    return JSONResponse(
        status_code=200,
        content={
            "success": attempt.get("filled", False),
            "message": attempt.get("message", ""),
            "dry_run": dry_run,
            "order_id": attempt.get("order_id"),
            "filled": attempt.get("filled"),
            "status": attempt.get("status"),
            "limit_credit": attempt.get("limit_credit"),
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
        return webhook_auth_json_response(auth_err, dry_run=dry_run, kind="entry")  # type: ignore[return-value]

    burst_raw = payload.get("burstCount") or payload.get("burst_count")
    if burst_raw is not None and settings.is_alpaca_paper:
        burst_n = _parse_burst_count(dict(payload), None, max_count=settings.burst_max_count)
        if burst_n > 1:
            guard = _burst_paper_guard(settings)
            if guard:
                return JSONResponse(  # type: ignore[return-value]
                    status_code=200,
                    content={"success": False, "message": guard, "dry_run": dry_run, "notifications": {}},
                )
            clean_payload = dict(payload)
            clean_payload["fillMode"] = "exercise"
            try:
                signal = coerce_signal(clean_payload, settings)
                build_order_from_signal(signal, settings)
            except (ValidationError, ValueError) as exc:
                return JSONResponse(  # type: ignore[return-value]
                    status_code=200,
                    content={
                        "success": False,
                        "message": f"Bad burst payload: {exc}",
                        "dry_run": dry_run,
                        "notifications": {},
                    },
                )
            interval_sec = float(clean_payload.get("burstInterval", clean_payload.get("burst_interval", 0)) or 0)
            skip_exits = bool(clean_payload.get("skipExits", clean_payload.get("skip_exits", True)))
            try:
                result = await run_burst_attempts(
                    settings,
                    signal,
                    burst_n,
                    interval_sec=interval_sec,
                    skip_exits=skip_exits,
                )
            except Exception as exc:
                logger.exception("Entry burst failed: %s", exc)
                return JSONResponse(  # type: ignore[return-value]
                    status_code=200,
                    content={"success": False, "message": str(exc), "dry_run": dry_run, "notifications": {}},
                )
            slim = _burst_response_payload(result)
            return JSONResponse(  # type: ignore[return-value]
                status_code=200,
                content={
                    "success": slim["success"],
                    "message": f"Burst complete: {slim['filled_count']}/{slim['burst_count']} filled",
                    "dry_run": dry_run,
                    "notifications": {},
                    **slim,
                },
            )

    try:
        signal = coerce_signal(payload, settings)
        build_order_from_signal(signal, settings)
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

    if settings.spread_mode_only and signal.is_short_put and signal.ticker.upper() == "SPY":
        record_activity(
            "entry",
            "rejected",
            "SPREAD_MODE_ONLY: naked SHORT_PUT disabled on SPY — use PUT_CREDIT_SPREAD",
            ticker=signal.ticker,
            extra={"action": str(signal.action)},
        )
        return JSONResponse(  # type: ignore[return-value]
            status_code=200,
            content={
                "success": False,
                "message": "SPREAD_MODE_ONLY: naked SHORT_PUT disabled on SPY — use PUT_CREDIT_SPREAD",
                "dry_run": dry_run,
                "notifications": {},
            },
        )

    if settings.spread_mode_only and signal.is_short_put and signal.ticker.upper() != "SPY":
        logger.info("Crush-It lane: allowing SHORT_PUT on %s (non-SPY)", signal.ticker)

    skip_reason = await check_spread_entry_allowed(settings, signal)
    if skip_reason:
        logger.warning("Entry skipped: %s", skip_reason)
        record_activity(
            "entry",
            "skipped",
            skip_reason,
            ticker=signal.ticker,
            extra={"action": str(signal.action), "strategy": str(signal.strategy)},
        )
        return JSONResponse(  # type: ignore[return-value]
            status_code=200,
            content={
                "success": False,
                "message": skip_reason,
                "dry_run": dry_run,
                "notifications": {},
            },
        )

    background_tasks.add_task(process_entry_alert, settings, signal)
    logger.info("Queued background entry for %s qty=%s", signal.ticker, signal.quantity)
    record_activity(
        "entry",
        "accepted",
        f"Queued {signal.strategy} qty={signal.quantity}",
        ticker=signal.ticker,
        extra={
            "action": str(signal.action),
            "strategy": str(signal.strategy),
            "dte": signal.dte_filter,
            "limit_credit": signal.limit_credit,
        },
    )
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


@app.post("/close-put")
async def close_put_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
) -> JSONResponse:
    """
    Conservative short-put close — batched buy-to-close with limit = bid + bidPremium.

    Use templates/tradingview-short-put-conservative-close.json from TradingView.
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
            content={"success": False, "message": "Alert body must be JSON object", "dry_run": dry_run},
        )

    provided, payload = extract_webhook_secret(payload, x_webhook_secret)
    auth_err = webhook_auth_error(provided, settings.webhook_secret)
    if auth_err:
        return webhook_auth_json_response(auth_err, dry_run=dry_run, kind="close-put")  # type: ignore[return-value]

    try:
        close_signal = ClosePutSignal.model_validate(payload)
    except (ValidationError, ValueError) as exc:
        return JSONResponse(
            status_code=200,
            content={"success": False, "message": f"Bad close payload: {exc}", "dry_run": dry_run},
        )

    background_tasks.add_task(process_conservative_close_put, settings, close_signal)
    logger.info(
        "Queued conservative close for %s qty=%s batch=%s",
        close_signal.ticker,
        close_signal.quantity,
        close_signal.batch_size,
    )
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "message": f"Close accepted — processing {close_signal.ticker} in batches of {close_signal.batch_size}",
            "dry_run": dry_run,
            "processing": True,
        },
    )


@app.post("/warning", response_model=WarningResponse)
async def warning_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
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
        return webhook_auth_json_response(auth_err, dry_run=dry_run, kind="warning")  # type: ignore[return-value]

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

    if not danger and not warning.force_auto_close:
        record_activity("warning", "no_danger", "Price not in danger zone", ticker=warning.ticker)
        return WarningResponse(
            danger_zone=False,
            risk_warning=None,
            distance_pct=round(distance * 100, 3),
            action_taken="no_danger",
            survival_odds_expire_otm=survival,
            protocol_notes=protocol_notes,
            notifications={},
        )

    if warning.force_auto_close and not danger:
        danger = True
        msg = msg or f"Forced close ({warning.ticker}) — stress / forceAutoClose"

    if not msg:
        record_activity("warning", "no_danger", "No warning message", ticker=warning.ticker)
        return WarningResponse(
            danger_zone=False,
            risk_warning=None,
            distance_pct=round(distance * 100, 3),
            action_taken="no_danger",
            survival_odds_expire_otm=survival,
            protocol_notes=protocol_notes,
            notifications={},
        )

    notes_copy = list(protocol_notes)
    background_tasks.add_task(
        process_warning_alert,
        settings,
        warning,
        msg,
        distance,
        survival,
        notes_copy,
    )
    logger.info("Queued background warning for %s", warning.ticker)
    record_activity(
        "warning",
        "accepted",
        msg[:200],
        ticker=warning.ticker,
        extra={"survival_pct": round(survival * 100, 1)},
    )
    return JSONResponse(  # type: ignore[return-value]
        status_code=200,
        content={
            "danger_zone": True,
            "risk_warning": msg,
            "distance_pct": round(distance * 100, 3),
            "action_taken": "accepted",
            "survival_odds_expire_otm": survival,
            "protocol_notes": notes_copy,
            "processing": True,
            "notifications": {},
        },
    )


@app.post("/webhook/stx-close")
async def stx_close_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
) -> JSONResponse:
    """
    STX strategy webhook — poll watcher state, evaluate kill matrix, or queue close.

    Modes (JSON field ``mode``):
      - poll: one read-only watcher cycle -> writes backend-config/stx_watcher_state.json
      - evaluate: fresh quote + recommendation (default, no orders)
      - execute: requires confirmClose=true; queues stx_kill order logic in background
    """
    settings = get_settings()
    dry_run = not settings.is_live

    if not _STX_MODULES_OK:
        logger.error("STX webhook rejected — strategy modules missing on server")
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "action_taken": "stx_unavailable",
                "message": "STX strategy modules not installed on this server",
                "dry_run": dry_run,
            },
        )

    try:
        payload = await request.json()
    except Exception as exc:
        logger.warning("Invalid STX webhook JSON: %s", exc)
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "action_taken": "invalid_json",
                "message": f"Invalid JSON body — use JSON alert message only: {exc}",
                "dry_run": dry_run,
            },
        )

    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "action_taken": "invalid_payload",
                "message": "Alert body must be a JSON object",
                "dry_run": dry_run,
            },
        )

    provided, payload = extract_webhook_secret(payload, x_webhook_secret)
    auth_err = webhook_auth_error(provided, settings.webhook_secret)
    if auth_err:
        logger.warning("STX webhook auth failed: %s", auth_err)
        return JSONResponse(
            status_code=401,
            content={
                "success": False,
                "action_taken": "auth_failed",
                "message": auth_err,
                "dry_run": dry_run,
            },
        )

    try:
        signal = StxCloseSignal.model_validate(payload)
    except ValidationError as exc:
        logger.warning("STX signal rejected: %s", exc)
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "action_taken": "invalid_signal",
                "message": f"Bad STX payload: {exc}",
                "dry_run": dry_run,
            },
        )

    if signal.mode == "open":
        if not signal.confirm_open:
            record_activity(
                "stx-close",
                "confirm_required",
                "open mode requires confirmOpen=true",
                ticker=signal.underlying,
            )
            return JSONResponse(
                status_code=200,
                content={
                    "success": False,
                    "action_taken": "confirm_required",
                    "message": "open mode requires confirmOpen=true in JSON payload",
                    "dry_run": dry_run,
                },
            )
        background_tasks.add_task(process_stx_open_execute, settings, signal)
        logger.info("Queued background STX open for %s", signal.underlying)
        record_activity(
            "stx-close",
            "accepted",
            f"Queued open {signal.underlying} put",
            ticker=signal.underlying,
        )
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "action_taken": "accepted",
                "message": "STX open signal accepted — processing in background",
                "dry_run": dry_run,
                "processing": True,
            },
        )

    if signal.mode == "execute":
        if not signal.confirm_close:
            record_activity(
                "stx-close",
                "confirm_required",
                "execute mode requires confirmClose=true",
                ticker=signal.underlying,
            )
            return JSONResponse(
                status_code=200,
                content={
                    "success": False,
                    "action_taken": "confirm_required",
                    "message": "execute mode requires confirmClose=true in JSON payload",
                    "dry_run": dry_run,
                },
            )
        background_tasks.add_task(process_stx_close_execute, settings, signal)
        logger.info("Queued background STX close execute for %s", signal.underlying)
        record_activity(
            "stx-close",
            "accepted",
            f"execute queued ({signal.underlying})",
            ticker=signal.underlying,
            extra={"mode": signal.mode},
        )
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "action_taken": "accepted",
                "message": "STX close signal accepted — processing in background",
                "mode": signal.mode,
                "processing": True,
                "dry_run": dry_run,
            },
        )

    try:
        result = await asyncio.to_thread(run_stx_close_evaluate, settings, signal)
    except Exception as exc:
        logger.exception("STX close evaluate failed: %s", exc)
        record_activity("stx-close", "failed", str(exc)[:200], ticker=signal.underlying)
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "action_taken": "failed",
                "message": str(exc),
                "dry_run": dry_run,
            },
        )

    rec = result.get("recommendation") or {}
    record_activity(
        "stx-close",
        str(result.get("action_taken", signal.mode)),
        f"{rec.get('action', 'hold')} limit={rec.get('limit_price')}",
        ticker=signal.underlying,
        extra={"symbol": result.get("symbol")},
    )
    logger.info(
        "STX %s complete %s rec=%s",
        signal.mode,
        result.get("symbol"),
        rec.get("action"),
    )
    return JSONResponse(status_code=200, content=result)

