"""TradersPost webhook relay.

TradersPost does not natively submit multi-leg put credit spreads as a single
order. This module relays the short put leg for monitoring/paper workflows, or
for strategies configured to Sell To Open short puts on action=buy (inverted
put semantics). Full spread execution is handled by TastytradeExecutor.
"""

from __future__ import annotations

import httpx

from spy_options_bridge.config import Settings
from spy_options_bridge.models.signal import ExecutionResult, ExecutionMode, StrikePair, TradingViewSignal
from spy_options_bridge.resolvers.strike_resolver import format_osi_symbol


class TradersPostRelay:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return self._settings.traderspost_enabled and bool(self._settings.traderspost_webhook_url)

    def build_short_put_payload(
        self,
        signal: TradingViewSignal,
        strikes: StrikePair,
    ) -> dict:
        """Build TradersPost-compatible payload for the short put leg."""
        short_symbol = format_osi_symbol(
            strikes.underlying,
            strikes.expiration,
            "put",
            strikes.short_strike,
        )
        payload: dict = {
            "ticker": short_symbol,
            "action": signal.action,
            "orderType": signal.order_type,
            "quantity": signal.quantity,
            "optionType": "put",
            "signalPrice": signal.signal_price,
            "extras": {
                "bridge": "spy-options-bridge",
                "strategy": "put_credit_spread",
                "leg": "short_put_only",
                "longStrike": strikes.long_strike,
                "note": "TradersPost single-leg relay; full spread via Tastytrade",
            },
        }
        if signal.extras:
            payload["extras"].update(signal.extras)
        return payload

    async def relay_short_put(
        self,
        signal: TradingViewSignal,
        strikes: StrikePair,
    ) -> ExecutionResult:
        if not self.enabled:
            return ExecutionResult(
                success=False,
                mode=self._settings.execution_mode,
                message="TradersPost relay disabled",
            )

        payload = self.build_short_put_payload(signal, strikes)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(self._settings.traderspost_webhook_url, json=payload)
            response.raise_for_status()

        return ExecutionResult(
            success=True,
            mode=self._settings.execution_mode,
            message="Relayed short put leg to TradersPost",
            strikes=strikes,
            details={"traderspost_status": response.status_code, "payload": payload},
        )
