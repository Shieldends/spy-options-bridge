"""Team email protocol — [SPY Command Center] subjects; user replies YES/NO (outbound only).

User inbox: shieldinc850@gmail.com — team reads replies manually.
Do NOT implement Gmail/IMAP read without explicit user permission.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from email_alerts import send_email_alert  # noqa: E402

PREFIX = "[SPY Command Center]"
USER_INBOX = "shieldinc850@gmail.com"
TeamKind = Literal["status", "permission", "report", "credit"]

SYNC_DIR = Path(r"C:\Users\Shiel\Projects\spy-hybrid-v3\sync")
GROK_OUTBOX = SYNC_DIR / "grok_outbox.md"
CURSOR_INBOX = SYNC_DIR / "cursor_inbox.md"
DESKTOP = Path(r"C:\Users\Shiel\Desktop")
REPORT_LIVE = DESKTOP / "LIVE-RUN-READINESS-REPORT.txt"
REPORT_FINAL = DESKTOP / "FINAL-TEAM-AUDIT.txt"

GENERAL_MIN_INTERVAL_SEC = 300
CYCLE_MIN_INTERVAL_SEC = 900

REPLY_PROTOCOL_DOC = (
    "Approve by replying YES to shieldinc850@gmail.com (same inbox). "
    "Reply NO to skip. Team reads your reply manually — no auto Gmail login. "
    "Future IMAP only with explicit user OK."
)

# Legacy constants (tests / imports)
SUBJECT_STATUS = f"{PREFIX} status"
SUBJECT_PERMISSION = f"{PREFIX} permission"
SUBJECT_REPORT = f"{PREFIX} report"
SUBJECT_CREDIT = f"{PREFIX} credit"
SUBJECT_ACTION_DONE = SUBJECT_CREDIT

_last_general_sent: float = 0.0
_last_cycle_sent: float = 0.0
_final_emailed_mtime: float | None = None


def _paragraph(text: str) -> str:
    return " ".join(text.split())


def _subject(kind: TeamKind, short_title: str) -> str:
    title = _paragraph(short_title).strip() or "Update"
    return f"{PREFIX} {kind}: {title}"


def _append_team_recall(subject: str) -> None:
    """Duplicate one-line recall to Grok outbox + Cursor inbox."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"{ts} | Email sent: {subject}\n"
    SYNC_DIR.mkdir(parents=True, exist_ok=True)
    for path in (GROK_OUTBOX, CURSOR_INBOX):
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)


def _general_rate_ok() -> bool:
    if _last_general_sent <= 0:
        return True
    return (time.time() - _last_general_sent) >= GENERAL_MIN_INTERVAL_SEC


def _cycle_rate_ok() -> bool:
    if _last_cycle_sent <= 0:
        return True
    return (time.time() - _last_cycle_sent) >= CYCLE_MIN_INTERVAL_SEC


def _record_general_send() -> None:
    global _last_general_sent
    _last_general_sent = time.time()


def _record_cycle_send() -> None:
    global _last_cycle_sent
    _last_cycle_sent = time.time()


def reset_rate_limits_for_tests() -> None:
    """Test helper — clear in-memory rate limit clocks."""
    global _last_general_sent, _last_cycle_sent, _final_emailed_mtime
    _last_general_sent = 0.0
    _last_cycle_sent = 0.0
    _final_emailed_mtime = None


def send_team_email(
    kind: TeamKind,
    short_title: str,
    body: str,
    *,
    settings: dict[str, Any] | None = None,
    bypass_rate_limit: bool = False,
    use_cycle_limit: bool = False,
) -> bool:
    """Send email; mirror subject to sync files for team recall."""
    if use_cycle_limit:
        if not bypass_rate_limit and not _cycle_rate_ok():
            return False
    elif kind != "permission":
        if not bypass_rate_limit and not _general_rate_ok():
            return False

    subject = _subject(kind, short_title)
    ok = send_email_alert(subject, _paragraph(body), settings=settings)
    if ok:
        if use_cycle_limit:
            _record_cycle_send()
        else:
            _record_general_send()
        _append_team_recall(subject)
    return ok


def send_status(
    short_title: str,
    paragraph: str,
    *,
    settings: dict[str, Any] | None = None,
    bypass_rate_limit: bool = False,
) -> bool:
    body = f"{_paragraph(paragraph)}\n\n(No reply needed.)"
    return send_team_email(
        "status",
        short_title,
        body,
        settings=settings,
        bypass_rate_limit=bypass_rate_limit,
    )


def send_permission_request(
    short_title: str,
    paragraph: str,
    *,
    settings: dict[str, Any] | None = None,
) -> bool:
    body = (
        f"{_paragraph(paragraph)}\n\n"
        "Reply YES to approve. Reply NO to skip.\n\n"
        f"{REPLY_PROTOCOL_DOC}"
    )
    return send_team_email("permission", short_title, body, settings=settings, bypass_rate_limit=True)


def send_report_summary(
    short_title: str,
    paragraph: str,
    *,
    paths: tuple[Path, ...] | list[Path] = (),
    settings: dict[str, Any] | None = None,
    bypass_rate_limit: bool = False,
    use_cycle_limit: bool = False,
) -> bool:
    path_lines = ""
    if paths:
        path_lines = "\n\nDesktop files:\n" + "\n".join(f"  • {p}" for p in paths)
    body = f"{_paragraph(paragraph)}{path_lines}\n\n(No reply needed.)"
    return send_team_email(
        "report",
        short_title,
        body,
        settings=settings,
        bypass_rate_limit=bypass_rate_limit,
        use_cycle_limit=use_cycle_limit,
    )


def send_credit_update(
    short_title: str,
    paragraph: str,
    *,
    settings: dict[str, Any] | None = None,
    bypass_rate_limit: bool = False,
) -> bool:
    body = f"{_paragraph(paragraph)}\n\n(No reply needed.)"
    return send_team_email(
        "credit",
        short_title,
        body,
        settings=settings,
        bypass_rate_limit=bypass_rate_limit,
    )


# Backward-compatible aliases
def send_permission(
    paragraph: str,
    *,
    headline: str = "Approval",
    settings: dict[str, Any] | None = None,
) -> bool:
    return send_permission_request(headline, paragraph, settings=settings)


def send_action_done(
    paragraph: str,
    *,
    headline: str = "Complete",
    settings: dict[str, Any] | None = None,
) -> bool:
    return send_credit_update(headline, paragraph, settings=settings)


def send_permission_sample(*, settings: dict[str, Any] | None = None) -> bool:
    return send_permission_request(
        "Sample permission test",
        "Sample from SPY Live Command Center. Reply YES if you received this and can approve "
        "team requests by email reply (no forms).",
        settings=settings,
    )


def send_test_and_permission_sample(*, settings: dict[str, Any] | None = None) -> tuple[bool, bool]:
    ok_status = send_status(
        f"Test status to {USER_INBOX}. Bridge team email is configured locally.",
        "Test email",
        settings=settings,
        bypass_rate_limit=True,
    )
    ok_perm = send_permission_sample(settings=settings)
    return ok_status, ok_perm


def notify_command_center_started(*, settings: dict[str, Any] | None = None) -> bool:
    return send_status(
        "Command Center started",
        "START TEAM: dual_sync_loop (60s), bridge_keepalive, redundant_test_loop (5 min). "
        "STOP ALL or close window stops workers.",
        settings=settings,
        bypass_rate_limit=True,
    )


def notify_redundant_cycle(
    cycle: int,
    pass_n: int,
    fail_n: int,
    summary: str,
    *,
    settings: dict[str, Any] | None = None,
) -> bool:
    log_path = DESKTOP / "REDUNDANT-TEST-LOG.txt"
    body = (
        f"Cycle {cycle}: PASS={pass_n} FAIL={fail_n}. {summary}. "
        f"Full log: {log_path}. Results: PRE-OPEN-TEST-RESULTS.txt on Desktop."
    )
    return send_report_summary(
        f"Pre-open test cycle {cycle}",
        body,
        paths=(DESKTOP / "PRE-OPEN-TEST-RESULTS.txt", log_path),
        settings=settings,
        use_cycle_limit=True,
    )


def _excerpt(path: Path, max_lines: int = 35) -> str:
    if not path.exists():
        return f"(file not found: {path})"
    lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    head = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        head += f"\n… ({len(lines) - max_lines} more lines on Desktop)"
    return head


def send_latest_reports_email(*, settings: dict[str, Any] | None = None) -> bool:
    """LIVE-RUN readiness + FINAL team audit — manual Command Center button."""
    parts: list[str] = []
    for path in (REPORT_LIVE, REPORT_FINAL):
        parts.append(f"=== {path.name} ===\n{_excerpt(path)}")
    body = "\n\n".join(parts)
    return send_report_summary(
        "LIVE run + FINAL audit",
        body,
        paths=(REPORT_LIVE, REPORT_FINAL),
        settings=settings,
        bypass_rate_limit=True,
    )


def try_email_final_report_available(*, settings: dict[str, Any] | None = None) -> bool:
    """Email once per FINAL-TEAM-AUDIT.txt mtime when file appears or updates."""
    global _final_emailed_mtime
    if not REPORT_FINAL.exists():
        return False
    mtime = REPORT_FINAL.stat().st_mtime
    if _final_emailed_mtime is not None and _final_emailed_mtime >= mtime:
        return False
    body = (
        f"FINAL team audit is on your Desktop.\n\n{_excerpt(REPORT_FINAL, max_lines=25)}\n\n"
        f"Also check: {REPORT_LIVE}"
    )
    ok = send_report_summary(
        "FINAL audit available",
        body,
        paths=(REPORT_FINAL, REPORT_LIVE),
        settings=settings,
    )
    if ok:
        _final_emailed_mtime = mtime
    return ok


def notify_entry_filled(order_id: str, credit: float, *, settings: dict[str, Any] | None = None) -> bool:
    return send_credit_update(
        "Entry filled",
        f"Alpaca entry order {order_id} filled at ${credit:.2f} credit. GTC exits placing.",
        settings=settings,
        bypass_rate_limit=True,
    )


def notify_burst_complete(
    filled: int,
    total: int,
    *,
    settings: dict[str, Any] | None = None,
) -> bool:
    return send_report_summary(
        "Burst complete",
        f"Thursday burst proof finished: {filled}/{total} paper put credit spreads filled.",
        settings=settings,
        bypass_rate_limit=True,
    )


def notify_health_fail(detail: str, *, settings: dict[str, Any] | None = None) -> bool:
    return send_status(
        "Health fail",
        f"Bridge health check failed. {detail}",
        settings=settings,
        bypass_rate_limit=True,
    )


def notify_premarket_reminder(
    summary: str,
    *,
    settings: dict[str, Any] | None = None,
) -> bool:
    return send_status(
        "Pre-market",
        f"Pre-market reminder before 9:30 ET. {summary}",
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
        if "entry filled" in t:
            return send_credit_update(title, combined, settings=settings, bypass_rate_limit=True)
        return send_report_summary(title, combined, settings=settings, bypass_rate_limit=True)
    if any(x in t for x in ("health", "fail", "alert", "keepalive", "ping")):
        return notify_health_fail(f"{title}. {body}", settings=settings)
    if "permission" in t or "approve" in t:
        return send_permission_request(title, combined, settings=settings)
    if "not filled" in t or "chasing" in t:
        return False
    return send_status(title, combined, settings=settings)
