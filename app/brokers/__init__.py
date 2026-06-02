from app.brokers.alpaca import AlpacaBroker
from app.brokers.base import BrokerClient
from app.brokers.tastytrade import TastytradeBroker
from app.config import Settings


def get_broker(settings: Settings) -> BrokerClient:
    if settings.broker.lower() == "alpaca":
        return AlpacaBroker(settings)
    return TastytradeBroker(settings)
