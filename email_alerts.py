"""Optional SMTP email alerts — fails silently when not configured."""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class EmailSettingsLike(Protocol):
    email_enabled: bool
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    email_from: str
    email_to: str


def _settings_from_env() -> dict[str, Any]:
    return {
        "email_enabled": os.getenv("EMAIL_ENABLED", "").lower() in ("1", "true", "yes"),
        "smtp_host": os.getenv("SMTP_HOST", ""),
        "smtp_port": int(os.getenv("SMTP_PORT", "587") or "587"),
        "smtp_user": os.getenv("SMTP_USER", ""),
        "smtp_password": os.getenv("SMTP_PASSWORD", ""),
        "email_from": os.getenv("EMAIL_FROM", ""),
        "email_to": os.getenv("EMAIL_TO", ""),
    }


def email_configured(settings: EmailSettingsLike | dict[str, Any] | None = None) -> bool:
    cfg = settings if settings is not None else _settings_from_env()
    if isinstance(cfg, dict):
        enabled = cfg.get("email_enabled", False)
        host = cfg.get("smtp_host", "")
        from_addr = cfg.get("email_from", "")
        to_addr = cfg.get("email_to", "")
    else:
        enabled = cfg.email_enabled
        host = cfg.smtp_host
        from_addr = cfg.email_from
        to_addr = cfg.email_to
    return bool(enabled and host and from_addr and to_addr)


def send_email_alert(
    subject: str,
    body: str,
    *,
    settings: EmailSettingsLike | dict[str, Any] | None = None,
) -> bool:
    """Send plain-text email. Returns True on success; False if disabled or on error (logged, never raises)."""
    cfg = settings if settings is not None else _settings_from_env()
    if not email_configured(cfg):
        return False

    if isinstance(cfg, dict):
        host = cfg["smtp_host"]
        port = int(cfg.get("smtp_port", 587))
        user = cfg.get("smtp_user", "")
        password = cfg.get("smtp_password", "")
        from_addr = cfg["email_from"]
        to_addr = cfg["email_to"]
    else:
        host = cfg.smtp_host
        port = cfg.smtp_port
        user = cfg.smtp_user
        password = cfg.smtp_password
        from_addr = cfg.email_from
        to_addr = cfg.email_to

    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            if user and password:
                server.login(user, password)
            server.sendmail(from_addr, [a.strip() for a in to_addr.split(",") if a.strip()], msg.as_string())
        logger.info("Email sent: %s", subject)
        return True
    except Exception as exc:
        logger.warning("Email failed (silent): %s — %s", subject, exc)
        return False
