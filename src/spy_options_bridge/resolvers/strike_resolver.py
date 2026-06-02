from datetime import datetime
from math import floor

from spy_options_bridge.guards.market_hours import ET, resolve_expiration_date
from spy_options_bridge.models.signal import OptionContractSpec, StrikePair, TradingViewSignal


def _spy_strike_increment(price: float) -> float:
    """SPY options use $1 strike increments across the typical 0DTE trading range."""
    _ = price  # reserved for future per-symbol increment tables
    return 1.0


def _round_strike(price: float, increment: float) -> float:
    return round(floor(price / increment) * increment, 2)


def resolve_atm_strike(underlying_price: float, increment: float | None = None) -> float:
    increment = increment or _spy_strike_increment(underlying_price)
    return _round_strike(underlying_price, increment)


def resolve_put_credit_strikes(
    signal: TradingViewSignal,
    underlying_price: float,
    *,
    reference_time: datetime | None = None,
) -> StrikePair:
    """Compute short/long put strikes from ATM using strike offsets."""
    spec = signal.option_contract or OptionContractSpec(underlying=signal.ticker)
    increment = _spy_strike_increment(underlying_price)
    atm = resolve_atm_strike(underlying_price, increment)

    short_strike = atm + (spec.strike_offset_short * increment)
    long_strike = atm + (spec.strike_offset_long * increment)

    if short_strike <= long_strike:
        raise ValueError(
            f"Invalid spread: short strike ({short_strike}) must be above long strike ({long_strike})"
        )

    expiration = resolve_expiration_date(spec.expiration, reference_time)

    return StrikePair(
        short_strike=short_strike,
        long_strike=long_strike,
        expiration=expiration,
        underlying=spec.underlying.upper(),
    )


def format_osi_symbol(underlying: str, expiration: str, option_type: str, strike: float) -> str:
    """Format Tastytrade/TradersPost OSI symbol: SPY 240510P516."""
    exp = datetime.strptime(expiration, "%Y-%m-%d")
    yy = exp.strftime("%y")
    mmdd = exp.strftime("%m%d")
    cp = "C" if option_type.lower().startswith("c") else "P"
    strike_int = int(strike) if strike == int(strike) else strike
    return f"{underlying.upper()} {yy}{mmdd}{cp}{strike_int}"
