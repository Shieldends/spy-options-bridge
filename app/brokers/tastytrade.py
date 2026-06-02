"""Tastytrade certification sandbox broker — multi-leg credit spread orders."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.brokers.base import BrokerClient
from app.config import Settings
from app.models import ExecutionResult, MLegOrderPackage
from app.risk_management import build_take_profit_close_payload
from app.spread_builder import compact_occ_to_tastytrade_symbol

logger = logging.getLogger(__name__)

CERT_URL = "https://api.cert.tastyworks.com"
    """
    Submits multi-leg spreads to Tastytrade cert/sandbox and manages exit orders.
    Docs: https://developer.tastytrade.com/order-submission/
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.tastytrade_api_base_url.rstrip("/") or CERT_URL

    async def _login_cert(self, client: httpx.AsyncClient) -> str:
        response = await client.post(
            "/sessions",
            json={
                "login": self._settings.tastytrade_username_resolved,
                "password": self._settings.tastytrade_password_resolved,
                "remember-me": True,
            },
        )
        response.raise_for_status()
        token = response.json().get("data", {}).get("session-token")
        if not token:
            raise ValueError("Cert login succeeded but no session-token in response")
        return token

    async def _auth_client(self) -> tuple[httpx.AsyncClient, dict[str, str]]:
        client = httpx.AsyncClient(base_url=self._base_url, timeout=30.0)
        token = await self._login_cert(client)
        headers = {
            "Authorization": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        return client, headers

    def _build_entry_order(self, order: MLegOrderPackage) -> dict:
        meta = order.metadata
        limit_credit = float(meta.get("limit_credit", 0.50))
        qty = int(order.qty)
        short_symbol = compact_occ_to_tastytrade_symbol(order.legs[0].symbol)
        long_symbol = compact_occ_to_tastytrade_symbol(order.legs[1].symbol)
        return {
            "time-in-force": "Day",
            "order-type": "Limit",
            "price": f"{limit_credit:.2f}",
            "price-effect": "Credit",
            "legs": [
                {
                    "instrument-type": "Equity Option",
                    "symbol": short_symbol,
                    "quantity": qty,
                    "action": "Sell to Open",
                },
                {
                    "instrument-type": "Equity Option",
                    "symbol": long_symbol,
                    "quantity": qty,
                    "action": "Buy to Open",
                },
            ],
        }

    async def _post_order_payload(
        self,
        payload: dict,
        *,
        use_dry_run_endpoint: bool,
    ) -> ExecutionResult:
        clean_payload = {k: v for k, v in payload.items() if not k.startswith("_")}
        account = self._settings.tastytrade_account_number
        path = f"/accounts/{account}/orders/dry-run" if use_dry_run_endpoint else f"/accounts/{account}/orders"

        client, headers = await self._auth_client()
        try:
            response = await client.post(path, headers=headers, json=clean_payload)
            try:
                body = response.json()
            except Exception:
                body = {"raw": response.text}

            if response.is_success:
                return ExecutionResult(
                    success=True,
                    message=f"Order accepted at {path}",
                    dry_run=use_dry_run_endpoint,
                    broker_response=body,
                )
            return ExecutionResult(
                success=False,
                message=f"Order rejected ({response.status_code})",
                broker_response=body,
            )
        finally:
            await client.aclose()

    async def submit_mleg_order(self, order: MLegOrderPackage, *, dry_run: bool) -> ExecutionResult:
        entry_payload = self._build_entry_order(order)

        if not self._settings.tastytrade_configured:
            return ExecutionResult(
                success=True if dry_run else False,
                message="Entry packaged (add Tastytrade cert credentials to submit)",
                dry_run=True,
                order_package=order,
                broker_response={"tastytrade_order": entry_payload},
            )

        try:
            result = await self._post_order_payload(entry_payload, use_dry_run_endpoint=dry_run)
            result.order_package = order
            if result.broker_response:
                result.broker_response["tastytrade_order"] = entry_payload
            return result
        except Exception as exc:
            logger.exception("Entry order failed")
            return ExecutionResult(
                success=False,
                message=str(exc),
                order_package=order,
                broker_response={"tastytrade_order": entry_payload},
            )

    async def submit_take_profit_close(
        self,
        entry: MLegOrderPackage,
        *,
        dry_run: bool,
        take_profit_pct: float | None = None,
    ) -> ExecutionResult:
        pct = take_profit_pct if take_profit_pct is not None else self._settings.take_profit_pct
        tp_payload = build_take_profit_close_payload(entry, take_profit_pct=pct)

        if not self._settings.tastytrade_configured:
            return ExecutionResult(
                success=True,
                message="Take-profit packaged (credentials required to submit)",
                dry_run=True,
                order_package=entry,
                broker_response={"tastytrade_close_order": tp_payload},
            )

        result = await self._post_order_payload(tp_payload, use_dry_run_endpoint=dry_run)
        result.order_package = entry
        if result.broker_response:
            result.broker_response["tastytrade_close_order"] = tp_payload
        return result
