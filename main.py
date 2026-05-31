"""
spy-options-bridge — TradingView → Tastytrade Cert Sandbox
Single-file cloud bridge for Render deployment.

Endpoints:
  GET  /health   — status check
  POST /entry    — multi-leg credit spread entry + GTC take-profit + GTC stop-loss
  POST /warning  — danger-zone alert only (no orders)
  POST /webhook  — alias for /entry (TradingView default)

Auth: X-Webhook-Secret header OR webhookSecret in JSON body (TradingView-friendly).

No streaming. No fill polling. GTC exit orders rest on Tastytrade servers.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from enum import Enum
from functools import lru_cache
from math import floor
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
CERT_URL = "https://api.cert.tastyworks.com"

# ── Settings ──────────────────────────────────────────────────────────────────


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    webhook_secret: str = Field(default="", alias="WEBHOOK_SECRET")
    execution_mode: str = Field(default="sandbox", alias="EXECUTION_MODE")

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
    default_dte_filter: str = Field(default="0dte", alias="DEFAULT_DTE_FILTER")

    discord_webhook_url: str = Field(default="", alias="DISCORD_WEBHOOK_URL")
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    default_underlying: str = Field(default="SPY", alias="DEFAULT_UNDERLYING")
    default_quantity: int = Field(default=1, alias="DEFAULT_QUANTITY")
    default_strike_offset_short: int = Field(default=-2, alias="DEFAULT_STRIKE_OFFSET_SHORT")
    default_strike_offset_long: int = Field(default=-3, alias="DEFAULT_STRIKE_OFFSET_LONG")
    default_limit_credit: float = Field(default=0.50, alias="DEFAULT_LIMIT_CREDIT")

    @property
    def is_live(self) -> bool:
        return self.execution_mode.lower() == "production"

    @property
    def username(self) -> str:
        return self.tastytrade_username or self.tastytrade_sandbox_username

    @property
    def password(self) -> str:
        return self.tastytrade_password or self.tastytrade_sandbox_password

    @property
    def configured(self) -> bool:
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
    strike_offset_short: int = Field(default=-2, alias="strikeOffsetShort")
    strike_offset_long: int = Field(default=-3, alias="strikeOffsetLong")
    short_strike: float | None = Field(default=None, alias="short_strike")
    long_strike: float | None = Field(default=None, alias="long_strike")
    limit_credit: float | None = Field(default=None, alias="limitCredit")
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
    short_strike: float = Field(alias="short_strike")
    long_strike: float | None = Field(default=None, alias="long_strike")

    model_config = {"populate_by_name": True}


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


def build_take_profit_payload(spread: SpreadPackage, take_profit_pct: float) -> dict:
    credit = float(spread.metadata["limit_credit"])
    close_debit = round(credit * take_profit_pct, 2)
    payload = build_close_spread_payload(spread, close_debit)
    payload["_meta"] = {
        "entry_credit": credit,
        "close_debit": close_debit,
        "profit_locked": round(credit - close_debit, 2),
    }
    return payload


def build_stop_loss_payload(spread: SpreadPackage, stop_loss_multiplier: float) -> dict:
    """
    GTC stop-loss safety net: buy back spread at N× entry credit (default 2×).

    Example: $0.50 credit entry → stop at $1.00 debit to close.
    """
    credit = float(spread.metadata["limit_credit"])
    close_debit = round(credit * stop_loss_multiplier, 2)
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


# ── Tastytrade cert broker ────────────────────────────────────────────────────


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


async def submit_order(settings: Settings, payload: dict, *, dry_run: bool) -> OrderResult:
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

    return OrderResult(success=False, message=f"Rejected ({r.status_code})", payload=clean, broker_response=body)


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
    return TradingViewSignal.model_validate(merged)


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="spy-options-bridge", version="4.0.1")

SECRET_BODY_KEYS = ("webhookSecret", "webhook_secret", "secret")


def _auth(provided: str | None, expected: str) -> None:
    if expected and provided != expected:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


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
    return {
        "status": "ok",
        "mode": s.execution_mode,
        "configured": str(s.configured),
        "api": s.tastytrade_api_base_url,
        "dte_filter_default": s.default_dte_filter,
    }


@app.post("/entry", response_model=EntryResponse)
@app.post("/webhook", response_model=EntryResponse)
async def entry_endpoint(
    request: Request,
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
) -> EntryResponse:
    """
    ENTRY endpoint — submit multi-leg credit spread + GTC take-profit + GTC stop-loss.
    TradingView should point here (or /webhook alias).
    """
    settings = get_settings()
    dry_run = not settings.is_live
    notifications: dict = {}

    payload = await request.json()
    provided, payload = extract_webhook_secret(payload, x_webhook_secret)
    _auth(provided, settings.webhook_secret)

    signal = coerce_signal(payload, settings)
    spread = build_spread(signal, settings)
    expiration = spread.metadata["expiration"]

    # Danger check on entry (informational — does not block order)
    danger = False
    risk_msg: str | None = None
    if signal.signal_price is not None:
        danger, risk_msg, _ = check_danger(
            signal.signal_price,
            float(spread.metadata["short_strike"]),
            settings.danger_zone_pct,
            signal.ticker,
        )
        if danger and risk_msg:
            notifications["risk"] = await notify(settings, "Entry Near Danger Zone", risk_msg, "CRITICAL")

    entry_payload = build_entry_payload(spread)
    entry = await submit_order(settings, entry_payload, dry_run=dry_run)

    if entry.success:
        notifications["entry"] = await notify(
            settings,
            "Entry Submitted",
            f"{signal.ticker} {spread.metadata['strategy']} exp={expiration} credit=${spread.metadata['limit_credit']}",
            "SUCCESS" if settings.is_live else "INFO",
        )

    take_profit: OrderResult | None = None
    if entry.success and settings.auto_take_profit:
        tp_payload = build_take_profit_payload(spread, settings.take_profit_pct)
        take_profit = await submit_order(settings, tp_payload, dry_run=dry_run)
        if take_profit.success:
            meta = tp_payload.get("_meta", {})
            notifications["take_profit"] = await notify(
                settings,
                "GTC Take-Profit Resting",
                f"{signal.ticker} close at ${meta.get('close_debit')} debit — locks ~${meta.get('profit_locked')}",
                "SUCCESS",
            )

    stop_loss: OrderResult | None = None
    if entry.success and settings.auto_stop_loss:
        sl_payload = build_stop_loss_payload(spread, settings.stop_loss_multiplier)
        stop_loss = await submit_order(settings, sl_payload, dry_run=dry_run)
        if stop_loss.success:
            meta = sl_payload.get("_meta", {})
            notifications["stop_loss"] = await notify(
                settings,
                "GTC Stop-Loss Resting",
                f"{signal.ticker} close at ${meta.get('close_debit')} debit — caps loss ~${meta.get('max_loss_estimate')}",
                "SUCCESS",
            )

    msg = entry.message
    if take_profit and take_profit.success:
        msg += " | GTC take-profit submitted"
    if stop_loss and stop_loss.success:
        msg += " | GTC stop-loss submitted"

    result = EntryResponse(
        success=entry.success,
        message=msg,
        dry_run=dry_run,
        expiration_resolved=expiration,
        danger_zone=danger,
        risk_warning=risk_msg,
        entry=entry,
        take_profit=take_profit,
        stop_loss=stop_loss,
        notifications=notifications,
    )
    if not entry.success:
        return JSONResponse(status_code=422, content=result.model_dump())  # type: ignore[return-value]
    return result


@app.post("/warning", response_model=WarningResponse)
async def warning_endpoint(
    request: Request,
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
) -> WarningResponse:
    """
    WARNING endpoint — danger zone check + alert only. No orders placed.
    Use a separate TradingView alert at your risk threshold price.
    """
    settings = get_settings()

    payload = await request.json()
    provided, payload = extract_webhook_secret(payload, x_webhook_secret)
    _auth(provided, settings.webhook_secret)

    warning = WarningSignal.model_validate(payload)

    danger, msg, distance = check_danger(
        warning.signal_price,
        warning.short_strike,
        settings.danger_zone_pct,
        warning.ticker,
    )

    notifications: dict = {}
    if danger and msg:
        notifications["warning"] = await notify(settings, "DANGER — Price Near Short Strike", msg, "CRITICAL")

    return WarningResponse(
        danger_zone=danger,
        risk_warning=msg or None,
        distance_pct=round(distance * 100, 3),
        notifications=notifications,
    )
