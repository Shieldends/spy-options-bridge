from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ExecutionMode(str, Enum):
    SANDBOX = "sandbox"
    PRODUCTION = "production"


class StrategyType(str, Enum):
    PUT_CREDIT_SPREAD = "put_credit_spread"


class OptionContractSpec(BaseModel):
    underlying: str = "SPY"
    expiration: str = "0dte"
    strategy: StrategyType = StrategyType.PUT_CREDIT_SPREAD
    strike_offset_short: int = Field(
        default=-2,
        alias="strikeOffsetShort",
        description="Strikes below ATM for short put (negative = OTM puts)",
    )
    strike_offset_long: int = Field(
        default=-3,
        alias="strikeOffsetLong",
        description="Strikes below ATM for long put (more OTM than short)",
    )
    limit_credit: float | None = Field(
        default=None,
        alias="limitCredit",
        description="Minimum net credit for the spread limit order",
    )

    model_config = {"populate_by_name": True}


class TradingViewSignal(BaseModel):
    """Normalized webhook payload from TradingView alerts."""

    ticker: str
    action: Literal["buy", "sell", "exit", "cancel", "add"]
    order_type: Literal["market", "limit", "stop", "stop_limit"] = "limit"
    quantity: int = 1
    signal_price: float | None = Field(default=None, alias="signalPrice")
    option_contract: OptionContractSpec | None = Field(default=None, alias="optionContract")
    extras: dict | None = None

    model_config = {"populate_by_name": True}

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper().replace(" ", "")


class StrikePair(BaseModel):
    short_strike: float
    long_strike: float
    expiration: str
    underlying: str


class ExecutionResult(BaseModel):
    success: bool
    mode: ExecutionMode
    message: str
    order_id: str | None = None
    strikes: StrikePair | None = None
    dry_run: bool = False
    details: dict | None = None
