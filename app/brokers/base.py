"""Broker adapter interface — swap Alpaca for Tastytrade without touching webhook code."""

from abc import ABC, abstractmethod

from app.models import ExecutionResult, MLegOrderPackage


class BrokerClient(ABC):
    @abstractmethod
    async def submit_mleg_order(self, order: MLegOrderPackage, *, dry_run: bool) -> ExecutionResult:
        """Submit a packaged multi-leg order to the broker API."""
