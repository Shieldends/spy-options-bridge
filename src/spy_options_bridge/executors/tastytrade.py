"""Direct Tastytrade execution for put credit spreads."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from functools import lru_cache

from tastytrade import Account, Session
from tastytrade.instruments import get_option_chain
from tastytrade.order import NewOrder, OrderAction, OrderTimeInForce, OrderType

from spy_options_bridge.config import Settings
from spy_options_bridge.models.signal import ExecutionMode, ExecutionResult, StrikePair, TradingViewSignal


class TastytradeExecutor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session: Session | None = None
        self._lock = asyncio.Lock()

    async def _get_session(self) -> Session:
        if self._session is None:
            self._session = Session(
                self._settings.tastytrade_username,
                self._settings.tastytrade_password,
                is_test=self._settings.tastytrade_is_test,
            )
        return self._session

    async def _find_put_contract(self, strikes: StrikePair, target_strike: float):
        session = await self._get_session()
        chain = await get_option_chain(session, strikes.underlying)
        exp = strikes.expiration
        if exp not in chain:
            available = sorted(chain.keys())[:5]
            raise ValueError(f"Expiration {exp} not in chain. Near dates: {available}")

        puts = [o for o in chain[exp] if o.option_type.value == "P"]
        match = min(puts, key=lambda o: abs(float(o.strike_price) - target_strike))
        if float(match.strike_price) != target_strike:
            raise ValueError(
                f"No exact put at {target_strike}; nearest is {match.strike_price}"
            )
        return match

    async def execute_put_credit_spread(
        self,
        signal: TradingViewSignal,
        strikes: StrikePair,
    ) -> ExecutionResult:
        if not self._settings.tastytrade_configured:
            return ExecutionResult(
                success=False,
                mode=self._settings.execution_mode,
                message="Tastytrade credentials not configured",
                strikes=strikes,
            )

        if signal.action == "exit":
            return await self._exit_spread(signal, strikes)

        if signal.action not in {"buy", "add"}:
            return ExecutionResult(
                success=False,
                mode=self._settings.execution_mode,
                message=f"Action '{signal.action}' not supported for put credit spread entry",
                strikes=strikes,
            )

        dry_run = self._settings.execution_mode != ExecutionMode.PRODUCTION

        async with self._lock:
            session = await self._get_session()
            account = await Account.get(session, self._settings.tastytrade_account_number)

            short_put = await self._find_put_contract(strikes, strikes.short_strike)
            long_put = await self._find_put_contract(strikes, strikes.long_strike)

            spec = signal.option_contract
            limit_credit = (
                spec.limit_credit
                if spec and spec.limit_credit is not None
                else self._settings.default_limit_credit
            )

            order = NewOrder(
                time_in_force=OrderTimeInForce.DAY,
                order_type=OrderType.LIMIT,
                legs=[
                    short_put.build_leg(signal.quantity, OrderAction.SELL_TO_OPEN),
                    long_put.build_leg(signal.quantity, OrderAction.BUY_TO_OPEN),
                ],
                price=Decimal(str(limit_credit)),
            )

            response = await account.place_order(session, order, dry_run=dry_run)

        order_id = None
        if hasattr(response, "order") and response.order:
            order_id = str(response.order.id)

        return ExecutionResult(
            success=True,
            mode=self._settings.execution_mode,
            message="Put credit spread dry-run preview" if dry_run else "Put credit spread submitted",
            order_id=order_id,
            strikes=strikes,
            dry_run=dry_run,
            details={
                "short_symbol": short_put.symbol,
                "long_symbol": long_put.symbol,
                "limit_credit": limit_credit,
                "quantity": signal.quantity,
            },
        )

    async def _exit_spread(
        self,
        signal: TradingViewSignal,
        strikes: StrikePair,
    ) -> ExecutionResult:
        dry_run = self._settings.execution_mode != ExecutionMode.PRODUCTION

        async with self._lock:
            session = await self._get_session()
            account = await Account.get(session, self._settings.tastytrade_account_number)

            short_put = await self._find_put_contract(strikes, strikes.short_strike)
            long_put = await self._find_put_contract(strikes, strikes.long_strike)

            order = NewOrder(
                time_in_force=OrderTimeInForce.DAY,
                order_type=OrderType.MARKET,
                legs=[
                    short_put.build_leg(signal.quantity, OrderAction.BUY_TO_CLOSE),
                    long_put.build_leg(signal.quantity, OrderAction.SELL_TO_CLOSE),
                ],
            )

            response = await account.place_order(session, order, dry_run=dry_run)

        order_id = None
        if hasattr(response, "order") and response.order:
            order_id = str(response.order.id)

        return ExecutionResult(
            success=True,
            mode=self._settings.execution_mode,
            message="Spread exit dry-run preview" if dry_run else "Spread exit submitted",
            order_id=order_id,
            strikes=strikes,
            dry_run=dry_run,
        )


@lru_cache
def get_tastytrade_executor(settings: Settings) -> TastytradeExecutor:
    return TastytradeExecutor(settings)
