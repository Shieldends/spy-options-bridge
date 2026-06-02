from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class SpreadStrategy(str, Enum):
    PUT_CREDIT_SPREAD = "put_credit_spread"
    CALL_CREDIT_SPREAD = "call_credit_spread"


class TradingViewSignal(BaseModel):
    """
    Supports two TradingView payload styles:

    A) Dynamic offsets (TrendSpider tripwire + auto strike math):
       signalPrice, strikeOffsetShort, strikeOffsetLong

    B) Explicit strikes (your tripwire template):
       action = PUT_CREDIT_SPREAD | CALL_CREDIT_SPREAD
       short_strike, long_strike, expiration (YYYY-MM-DD or YYMMDD)
    """

    ticker: str
    strategy: SpreadStrategy | None = None
    signal_price: float | None = Field(default=None, alias="signalPrice")
    quantity: int = 1
    strike_offset_short: int = Field(default=-2, alias="strikeOffsetShort")
    strike_offset_long: int = Field(default=-3, alias="strikeOffsetLong")
    short_strike: float | None = Field(default=None, alias="short_strike")
    long_strike: float | None = Field(default=None, alias="long_strike")
    limit_credit: float | None = Field(default=None, alias="limitCredit")
    expiration: str = Field(default="0dte")
    action: str = "enter"

    model_config = {"populate_by_name": True}

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper().replace(" ", "")

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, value: str | None) -> str:
        if value is None:
            return "enter"
        return str(value).upper()

    @model_validator(mode="after")
    def resolve_strategy_and_strikes(self) -> "TradingViewSignal":
        action = self.action
        if action in {"PUT_CREDIT_SPREAD", "PUT CREDIT SPREAD"}:
            self.strategy = SpreadStrategy.PUT_CREDIT_SPREAD
        elif action in {"CALL_CREDIT_SPREAD", "CALL CREDIT SPREAD"}:
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


class SpreadLeg(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    position_intent: Literal[
        "buy_to_open",
        "sell_to_open",
        "buy_to_close",
        "sell_to_close",
    ]
    ratio_qty: str = "1"


class MLegOrderPackage(BaseModel):
    """Broker-agnostic multi-leg spread order."""

    order_class: Literal["mleg"] = "mleg"
    qty: str
    type: Literal["limit"] = "limit"
    limit_price: str
    time_in_force: Literal["day"] = "day"
    legs: list[SpreadLeg]
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    success: bool
    message: str
    dry_run: bool = False
    order_package: MLegOrderPackage | None = None
    broker_response: dict[str, Any] | None = None


class TradeFlowResult(BaseModel):
    """Full entry → take-profit → risk alert response."""

    success: bool
    message: str
    dry_run: bool = False
    entry: ExecutionResult | None = None
    take_profit: ExecutionResult | None = None
    risk_warning: str | None = None
    danger_zone: bool = False
    notifications: dict[str, Any] = Field(default_factory=dict)
