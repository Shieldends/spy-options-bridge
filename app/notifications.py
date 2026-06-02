"""Discord + Telegram webhook alerts."""

from __future__ import annotations

import logging
from enum import Enum

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class AlertLevel(str, Enum):
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class Notifier:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self._settings.discord_webhook_url or self._settings.telegram_configured)

    async def send(self, title: str, body: str, level: AlertLevel = AlertLevel.INFO) -> dict:
        message = f"**[{level.value}] {title}**\n{body}"
        results: dict = {"discord": None, "telegram": None}

        if not self.enabled:
            logger.info("Notification (no webhook configured): %s — %s", title, body)
            return results

        async with httpx.AsyncClient(timeout=15.0) as client:
            if self._settings.discord_webhook_url:
                try:
                    resp = await client.post(
                        self._settings.discord_webhook_url,
                        json={"content": message[:2000]},
                    )
                    results["discord"] = resp.status_code
                except Exception as exc:
                    logger.exception("Discord notification failed")
                    results["discord"] = str(exc)

            if self._settings.telegram_configured:
                url = (
                    f"https://api.telegram.org/bot{self._settings.telegram_bot_token}"
                    f"/sendMessage"
                )
                try:
                    resp = await client.post(
                        url,
                        json={
                            "chat_id": self._settings.telegram_chat_id,
                            "text": message[:4000],
                            "parse_mode": "Markdown",
                        },
                    )
                    results["telegram"] = resp.status_code
                except Exception as exc:
                    logger.exception("Telegram notification failed")
                    results["telegram"] = str(exc)

        return results
