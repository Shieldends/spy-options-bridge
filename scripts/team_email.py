"""Team email protocol — [SPY-LIVE] subjects; user replies YES/NO (outbound only, no IMAP).

User inbox: shieldinc850@gmail.com — team reads replies manually.
Do NOT implement Gmail/IMAP read without explicit user permission.
"""

from __future__ import annotations

from typing import Any

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from email_alerts import send_email_alert  # noqa: E402

PREFIX = "[SPY-LIVE]"
USER_INBOX = "shieldinc850@gmail.com"

SUBJECT_STATUS = f"{PREFIX} STATUS"
SUBJECT_PERMISSION = f"{PREFIX} PERMISSION NEEDED"
SUBJECT_ACTION_DONE = f"{PREFIX} ACTION DONE"

REPLY_PROTOCOL_DOC = (
    "Approve by replying YES to shieldinc850@gmail.com (same inbox). "
    "Reply NO to skip. Team reads your reply manually — no auto Gmail login. "
    "Future IMAP only with explicit user OK."
)


def _paragraph(text: str) -> str:
    return " ".join(text.split())


def send_status(
    paragraph: str,
    *,
    headline: str = "Update",
    settings: dict[str, Any] | None = None,
) -> bool:
    body = f"{_paragraph(paragraph)}\n\n(No reply needed.)"
    return send_email_alert(f"{SUBJECT_STATUS} — {headline}", body, settings=settings)


def send_permission(
    paragraph: str,
    *,
    headline: str = "Approval",
    settings: dict[str, Any] | None = None,
) -> bool:
    body = (
        f"{_paragraph(paragraph)}\n\n"
        "Reply YES to approve. Reply NO to skip.\n\n"
        f"{REPLY_PROTOCOL_DOC}"
    )
    return send_email_alert(f"{SUBJECT_PERMISSION} — {headline}", body, settings=settings)


def send_action_done(
    paragraph: str,
    *,
    headline: str = "Complete",
    settings: dict[str, Any] | None = None,
) -> bool:
    body = f"{_paragraph(paragraph)}\n\n(No reply needed.)"
    return send_email_alert(f"{SUBJECT_ACTION_DONE} — {headline}", body, settings=settings)


def send_permission_sample(*, settings: dict[str, Any] | None = None) -> bool:
    return send_permission(
        "Sample from SPY Live Command. Reply YES if you received this and can approve "
        "team requests by email reply (no forms).",
        headline="Sample permission test",
        settings=settings,
    )


def send_test_and_permission_sample(*, settings: dict[str, Any] | None = None) -> tuple[bool, bool]:
    ok_status = send_status(
        f"Test status to {USER_INBOX}. Bridge team email is configured locally.",
        headline="Test email",
        settings=settings,
    )
    ok_perm = send_permission_sample(settings=settings)
    return ok_status, ok_perm


def notify_entry_filled(order_id: str, credit: float, *, settings: dict[str, Any] | None = None) -> bool:
    return send_action_done(
        f"Alpaca entry order {order_id} filled at ${credit:.2f} credit. GTC exits placing.",
        headline="Entry filled",
        settings=settings,
    )


def notify_burst_complete(
    filled: int,
    total: int,
    *,
    settings: dict[str, Any] | None = None,
) -> bool:
    return send_action_done(
        f"Thursday burst proof finished: {filled}/{total} paper put credit spreads filled.",
        headline="Burst complete",
        settings=settings,
    )


def notify_health_fail(detail: str, *, settings: dict[str, Any] | None = None) -> bool:
    return send_status(
        f"Bridge health check failed. {detail}",
        headline="Health fail",
        settings=settings,
    )


def notify_premarket_reminder(
    summary: str,
    *,
    settings: dict[str, Any] | None = None,
) -> bool:
    return send_status(
        f"Pre-market reminder before 9:30 ET. {summary}",
        headline="Pre-market",
        settings=settings,
    )


def bridge_notify(
    title: str,
    body: str,
    *,
    level: str = "INFO",
    settings: dict[str, Any] | None = None,
) -> bool:
    """Map legacy notify titles to team protocol subjects."""
    t = title.lower()
    combined = _paragraph(f"[{level}] {body}") if body else _paragraph(f"[{level}] {title}")

    if "entry filled" in t or "burst complete" in t:
        return send_action_done(combined, headline=title, settings=settings)
    if any(x in t for x in ("health", "fail", "alert", "keepalive", "ping")):
        return notify_health_fail(f"{title}. {body}", settings=settings)
    if "permission" in t or "approve" in t:
        return send_permission(combined, headline=title, settings=settings)
    if "not filled" in t or "chasing" in t:
        return False
    return send_status(combined, headline=title, settings=settings)
