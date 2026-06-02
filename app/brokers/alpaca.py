"""Alpaca Paper/Live REST adapter for multi-leg (mleg) options orders."""

from __future__ import annotations

import httpx

from app.brokers.base import BrokerClient
from app.config import Settings
from app.models import ExecutionResult, MLegOrderPackage


class AlpacaBroker(BrokerClient):
    """
    POST /v2/orders with order_class=mleg

    Official docs:
    - https://docs.alpaca.markets/docs/options-level-3-trading
    - https://docs.alpaca.markets/reference/postorder
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.apca_api_base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "Apca-Api-Key-Id": self._settings.alpaca_key_id,
            "Apca-Api-Secret-Key": self._settings.alpaca_secret,
        }

    def _to_alpaca_payload(self, order: MLegOrderPackage) -> dict:
        """Convert our neutral package to Alpaca's exact JSON body."""
        return {
            "order_class": order.order_class,
            "qty": order.qty,
            "type": order.type,
            "limit_price": order.limit_price,
            "time_in_force": order.time_in_force,
            "legs": [leg.model_dump() for leg in order.legs],
        }

    async def submit_mleg_order(self, order: MLegOrderPackage, *, dry_run: bool) -> ExecutionResult:
        payload = self._to_alpaca_payload(order)

        if dry_run:
            return ExecutionResult(
                success=True,
                message="Sandbox mode — order packaged but not sent to Alpaca",
                dry_run=True,
                order_package=order,
                broker_response={"preview_payload": payload},
            )

        if not self._settings.alpaca_configured:
            return ExecutionResult(
                success=False,
                message="Alpaca credentials missing. Set APCA_API_KEY_ID and APCA_API_SECRET_KEY in .env",
                order_package=order,
            )

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._base_url}/v2/orders",
                headers=self._headers(),
                json=payload,
            )

        try:
            body = response.json()
        except Exception:
            body = {"raw": response.text}

        if response.is_success:
            return ExecutionResult(
                success=True,
                message="Multi-leg order submitted to Alpaca",
                dry_run=False,
                order_package=order,
                broker_response=body,
            )

        return ExecutionResult(
            success=False,
            message=f"Alpaca rejected order ({response.status_code})",
            order_package=order,
            broker_response=body,
        )
