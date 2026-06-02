"""
DEPRECATED for Render deploy — use root main.py instead:
  C:\\Users\\Shiel\\spy-options-bridge\\main.py
"""
import logging
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.models import TradeFlowResult
from app.router import route_signal
from app.spread_builder import coerce_signal

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="spy-options-bridge",
    description="Legacy modular app — Render uses root main.py (Alpaca Paper)",
    version="3.1.0",
)


def _check_secret(provided: str | None, expected: str) -> None:
    if expected and provided != expected:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


@app.get("/health")
async def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "broker": settings.broker,
        "mode": settings.execution_mode,
        "auto_take_profit": str(settings.auto_take_profit),
        "tastytrade_configured": str(settings.tastytrade_configured),
        "notifications": str(bool(settings.discord_webhook_url or settings.telegram_configured)),
    }


@app.post("/webhook", response_model=TradeFlowResult)
@app.post("/webhook/tradingview", response_model=TradeFlowResult)
async def tradingview_webhook(
    request: Request,
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
) -> TradeFlowResult:
    settings = get_settings()
    _check_secret(x_webhook_secret, settings.webhook_secret)

    try:
        payload: dict[str, Any] = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    signal = coerce_signal(payload, settings)
    result = await route_signal(signal, settings)

    if not result.success:
        return JSONResponse(status_code=422, content=result.model_dump())  # type: ignore[return-value]

    return result


@app.post("/preview/spread")
async def preview_spread(payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    signal = coerce_signal(payload, settings)
    result = await route_signal(signal, settings)
    return result.model_dump()
