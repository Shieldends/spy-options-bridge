import logging
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from spy_options_bridge import __version__
from spy_options_bridge.config import get_settings
from spy_options_bridge.models.signal import ExecutionResult, OptionContractSpec, TradingViewSignal
from spy_options_bridge.router import OrderRouter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="spy-options-bridge",
    description="TradingView webhook router for SPY put credit spreads via Tastytrade",
    version=__version__,
)


def _verify_secret(provided: str | None, expected: str) -> None:
    if expected and expected != "change-me" and provided != expected:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


def _coerce_signal(payload: dict[str, Any]) -> TradingViewSignal:
    settings = get_settings()
    if "optionContract" not in payload and payload.get("action") in {"buy", "add", "exit"}:
        payload = {
            **payload,
            "optionContract": {
                "underlying": settings.default_underlying,
                "expiration": "0dte",
                "strategy": "put_credit_spread",
                "strikeOffsetShort": settings.default_strike_offset_short,
                "strikeOffsetLong": settings.default_strike_offset_long,
                "limitCredit": settings.default_limit_credit,
            },
        }
    if "quantity" not in payload:
        payload["quantity"] = settings.default_quantity
    return TradingViewSignal.model_validate(payload)


@app.get("/health")
async def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "mode": settings.execution_mode.value,
        "tastytrade_configured": str(settings.tastytrade_configured),
        "traderspost_enabled": str(settings.traderspost_enabled),
    }


@app.post("/webhook/tradingview", response_model=ExecutionResult)
async def tradingview_webhook(
    request: Request,
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
) -> ExecutionResult:
    settings = get_settings()
    _verify_secret(x_webhook_secret, settings.webhook_secret)

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    signal = _coerce_signal(payload)
    router = OrderRouter(settings)
    result = await router.route(signal)

    if not result.success:
        return JSONResponse(status_code=422, content=result.model_dump())

    return result


@app.post("/webhook/traderspost/preview")
async def traderspost_preview(payload: dict[str, Any]) -> dict[str, Any]:
    """Preview the TradersPost relay payload without sending."""
    from spy_options_bridge.executors.traderspost import TradersPostRelay
    from spy_options_bridge.resolvers.strike_resolver import resolve_put_credit_strikes

    signal = _coerce_signal(payload)
    if signal.signal_price is None:
        raise HTTPException(status_code=400, detail="signalPrice required")

    strikes = resolve_put_credit_strikes(signal, signal.signal_price)
    relay = TradersPostRelay(get_settings())
    return relay.build_short_put_payload(signal, strikes)


def run() -> None:
    import uvicorn

    uvicorn.run(
        "spy_options_bridge.main:app",
        host="0.0.0.0",
        port=8080,
        reload=False,
    )


if __name__ == "__main__":
    run()
