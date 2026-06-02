"""
Entry → GTC take-profit → risk alerts (no fill polling).

Flow per webhook:
  1. Danger zone check → log + notify
  2. Submit entry spread to Tastytrade cert
  3. Immediately submit GTC take-profit at 50% credit (broker handles rest)
  4. Notify on entry + take-profit submission
"""

from __future__ import annotations

import logging
from typing import Any

from app.brokers import get_broker
from app.brokers.tastytrade import TastytradeBroker
from app.config import Settings
from app.models import ExecutionResult, TradeFlowResult, TradingViewSignal
from app.notifications import AlertLevel, Notifier
from app.risk_management import check_danger_zone
from app.spread_builder import build_credit_spread_order

logger = logging.getLogger(__name__)


async def route_signal(signal: TradingViewSignal, settings: Settings) -> TradeFlowResult:
    notifier = Notifier(settings)
    order = build_credit_spread_order(signal, settings)
    broker = get_broker(settings)
    dry_run = not settings.is_live
    notifications: dict[str, Any] = {}

    short_strike = float(order.metadata["short_strike"])

    # ── Step 1: Danger zone check ───────────────────────────────────────────
    risk_warning: str | None = None
    danger_zone = False
    if signal.signal_price is not None:
        danger_zone, risk_warning = check_danger_zone(
            signal.signal_price,
            short_strike,
            danger_pct=settings.danger_zone_pct,
            ticker=signal.ticker,
        )
        if danger_zone and risk_warning:
            notifications["risk"] = await notifier.send(
                "DANGER — Price Near Short Strike",
                risk_warning,
                AlertLevel.CRITICAL,
            )

    # ── Step 2: Submit entry spread ─────────────────────────────────────────
    entry = await broker.submit_mleg_order(order, dry_run=dry_run)

    if entry.success:
        notifications["entry"] = await notifier.send(
            "Entry Submitted",
            (
                f"{signal.ticker} {order.metadata.get('strategy')}\n"
                f"Short: ${short_strike} | Long: ${order.metadata.get('long_strike')}\n"
                f"Credit: ${order.metadata.get('limit_credit')} | Mode: {settings.execution_mode}"
            ),
            AlertLevel.SUCCESS if settings.is_live else AlertLevel.INFO,
        )

    # ── Step 3: GTC take-profit at 50% — submit immediately, no polling ─────
    take_profit: ExecutionResult | None = None
    if entry.success and settings.auto_take_profit and isinstance(broker, TastytradeBroker):
        take_profit = await broker.submit_take_profit_close(order, dry_run=dry_run)
        if take_profit.success:
            meta = (take_profit.broker_response or {}).get("tastytrade_close_order", {})
            tp_meta = meta.get("_meta", {})
            notifications["take_profit"] = await notifier.send(
                "Take-Profit GTC Resting",
                (
                    f"{signal.ticker} — GTC close at ${tp_meta.get('close_debit', '?')} debit\n"
                    f"Locks ~${tp_meta.get('profit_locked', '?')} profit on broker servers"
                ),
                AlertLevel.SUCCESS,
            )
        else:
            notifications["take_profit"] = await notifier.send(
                "Take-Profit Submit Failed",
                take_profit.message,
                AlertLevel.WARNING,
            )

    message = entry.message
    if take_profit and take_profit.success:
        message = f"{message} | GTC take-profit submitted"
    if danger_zone:
        message = f"{message} | RISK WARNING ACTIVE"

    return TradeFlowResult(
        success=entry.success,
        message=message,
        dry_run=dry_run,
        entry=entry,
        take_profit=take_profit,
        risk_warning=risk_warning,
        danger_zone=danger_zone,
        notifications=notifications,
    )
