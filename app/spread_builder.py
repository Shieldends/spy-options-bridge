"""Build broker-agnostic multi-leg credit spread orders from TradingView signals."""

from datetime import datetime
from math import floor
from zoneinfo import ZoneInfo

from app.config import Settings
from app.models import MLegOrderPackage, SpreadLeg, SpreadStrategy, TradingViewSignal

ET = ZoneInfo("America/New_York")


def _strike_increment(_underlying: str, _price: float) -> float:
    return 1.0


def _round_strike(price: float, increment: float) -> float:
    return round(floor(price / increment) * increment, 2)


def _normalize_expiration(expiration: str, reference: datetime | None = None) -> str:
    reference = reference or datetime.now(tz=ET)
    normalized = str(expiration).strip()

    if normalized.lower() in {"0dte", "+0 days", "today", ""}:
        return reference.strftime("%Y-%m-%d")

    if len(normalized) == 10 and normalized[4] == "-":
        return normalized

    # YYMMDD from TradingView tripwire payloads
    if len(normalized) == 6 and normalized.isdigit():
        return datetime.strptime(normalized, "%y%m%d").strftime("%Y-%m-%d")

    raise ValueError(f"Unsupported expiration: {expiration}")


def format_occ_symbol(underlying: str, expiration_yyyy_mm_dd: str, option_type: str, strike: float) -> str:
    exp = datetime.strptime(expiration_yyyy_mm_dd, "%Y-%m-%d")
    yymmdd = exp.strftime("%y%m%d")
    cp = "C" if option_type == "call" else "P"
    strike_encoded = f"{int(round(strike * 1000)):08d}"
    return f"{underlying.upper()}{yymmdd}{cp}{strike_encoded}"


def compact_occ_to_tastytrade_symbol(compact_symbol: str) -> str:
    """SPY260603P00579000 → SPY   260603P00579000"""
    idx = next(i for i, ch in enumerate(compact_symbol) if ch.isdigit())
    root = compact_symbol[:idx].ljust(6)
    return root + compact_symbol[idx:]


def _resolve_strikes_from_offsets(
    signal: TradingViewSignal,
    underlying_price: float,
) -> tuple[float, float, str, str]:
    increment = _strike_increment(signal.ticker, underlying_price)
    atm = _round_strike(underlying_price, increment)
    short_strike = atm + (signal.strike_offset_short * increment)
    long_strike = atm + (signal.strike_offset_long * increment)
    expiration = _normalize_expiration(signal.expiration)

    if signal.strategy == SpreadStrategy.PUT_CREDIT_SPREAD:
        if short_strike <= long_strike:
            raise ValueError(
                f"Put credit spread: short strike ({short_strike}) must be above long ({long_strike})"
            )
        option_type = "put"
    else:
        if short_strike >= long_strike:
            raise ValueError(
                f"Call credit spread: short strike ({short_strike}) must be below long ({long_strike})"
            )
        option_type = "call"

    return short_strike, long_strike, expiration, option_type


def build_credit_spread_order(signal: TradingViewSignal, settings: Settings) -> MLegOrderPackage:
    """
    Package a single atomic multi-leg credit spread.

    Put credit spread:
      sell_to_open short leg (higher strike put)
      buy_to_open  long leg  (lower strike put, protection)

    Call credit spread:
      sell_to_open short leg (lower strike call)
      buy_to_open  long leg  (higher strike call, protection)
    """
    if signal.uses_explicit_strikes:
        short_strike = float(signal.short_strike)  # type: ignore[arg-type]
        long_strike = float(signal.long_strike)  # type: ignore[arg-type]
        expiration = _normalize_expiration(signal.expiration)
        option_type = "put" if signal.strategy == SpreadStrategy.PUT_CREDIT_SPREAD else "call"
    else:
        short_strike, long_strike, expiration, option_type = _resolve_strikes_from_offsets(
            signal, signal.signal_price  # type: ignore[arg-type]
        )

    short_symbol = format_occ_symbol(signal.ticker, expiration, option_type, short_strike)
    long_symbol = format_occ_symbol(signal.ticker, expiration, option_type, long_strike)

    limit_credit = signal.limit_credit if signal.limit_credit is not None else settings.default_limit_credit
    limit_price = f"{-abs(limit_credit):.2f}"  # Alpaca convention; Tastytrade broker converts separately

    legs = [
        SpreadLeg(
            symbol=short_symbol,
            ratio_qty="1",
            side="sell",
            position_intent="sell_to_open",
        ),
        SpreadLeg(
            symbol=long_symbol,
            ratio_qty="1",
            side="buy",
            position_intent="buy_to_open",
        ),
    ]

    return MLegOrderPackage(
        qty=str(signal.quantity),
        limit_price=limit_price,
        legs=legs,
        metadata={
            "underlying": signal.ticker,
            "strategy": signal.strategy.value if signal.strategy else "put_credit_spread",
            "expiration": expiration,
            "short_strike": short_strike,
            "long_strike": long_strike,
            "signal_price": signal.signal_price,
            "limit_credit": limit_credit,
        },
    )


def coerce_signal(payload: dict, settings: Settings) -> TradingViewSignal:
    merged = {
        "strikeOffsetShort": settings.default_strike_offset_short,
        "strikeOffsetLong": settings.default_strike_offset_long,
        "limitCredit": settings.default_limit_credit,
        **payload,
    }
    if "ticker" not in merged:
        merged["ticker"] = settings.default_underlying
    if "quantity" not in merged:
        merged["quantity"] = settings.default_quantity
    return TradingViewSignal.model_validate(merged)
