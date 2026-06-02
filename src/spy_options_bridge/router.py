"""Order routing orchestrator."""

from __future__ import annotations

import logging

from spy_options_bridge.config import Settings
from spy_options_bridge.executors.tastytrade import TastytradeExecutor
from spy_options_bridge.executors.traderspost import TradersPostRelay
from spy_options_bridge.guards.market_hours import is_regular_market_hours
from spy_options_bridge.models.signal import ExecutionResult, TradingViewSignal
from spy_options_bridge.resolvers.strike_resolver import resolve_put_credit_strikes

logger = logging.getLogger(__name__)


class OrderRouter:
    def __init__(
        self,
        settings: Settings,
        tastytrade: TastytradeExecutor | None = None,
        traderspost: TradersPostRelay | None = None,
    ) -> None:
        self._settings = settings
        self._tastytrade = tastytrade or TastytradeExecutor(settings)
        self._traderspost = traderspost or TradersPostRelay(settings)

    async def route(self, signal: TradingViewSignal) -> ExecutionResult:
        if self._settings.enforce_market_hours:
            is_open, reason = is_regular_market_hours(
                allow_extended=self._settings.allow_extended_hours
            )
            if not is_open:
                logger.warning("Signal rejected: %s", reason)
                return ExecutionResult(
                    success=False,
                    mode=self._settings.execution_mode,
                    message=f"Outside execution window: {reason}",
                )

        if signal.signal_price is None:
            return ExecutionResult(
                success=False,
                mode=self._settings.execution_mode,
                message="signalPrice is required for dynamic strike resolution",
            )

        try:
            strikes = resolve_put_credit_strikes(signal, signal.signal_price)
        except ValueError as exc:
            return ExecutionResult(
                success=False,
                mode=self._settings.execution_mode,
                message=str(exc),
            )

        logger.info(
            "Routing %s %s spread: short=%s long=%s exp=%s",
            signal.ticker,
            signal.action,
            strikes.short_strike,
            strikes.long_strike,
            strikes.expiration,
        )

        tasty_result = await self._tastytrade.execute_put_credit_spread(signal, strikes)
        if not tasty_result.success:
            return tasty_result

        relay_result = None
        if self._traderspost.enabled:
            try:
                relay_result = await self._traderspost.relay_short_put(signal, strikes)
            except Exception as exc:
                logger.exception("TradersPost relay failed")
                tasty_result.details = tasty_result.details or {}
                tasty_result.details["traderspost_error"] = str(exc)

        if relay_result:
            tasty_result.details = tasty_result.details or {}
            tasty_result.details["traderspost_relay"] = relay_result.details

        return tasty_result
