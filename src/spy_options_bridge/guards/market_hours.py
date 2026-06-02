from datetime import datetime
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

ET = ZoneInfo("America/New_York")
NYSE = xcals.get_calendar("XNYS")


def is_regular_market_hours(
    now: datetime | None = None,
    *,
    allow_extended: bool = False,
) -> tuple[bool, str]:
    """Return (is_open, reason) for US equity regular session."""
    now = now or datetime.now(tz=ET)
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    else:
        now = now.astimezone(ET)

    session_date = now.date()
    if not NYSE.is_session(session_date):
        return False, f"Market closed: not a trading session ({session_date})"

    open_time = NYSE.session_open(session_date).astimezone(ET)
    close_time = NYSE.session_close(session_date).astimezone(ET)

    if allow_extended:
        # Pre/post not modeled separately; treat session window as open
        if now < open_time:
            return False, f"Before regular open ({open_time.strftime('%H:%M')} ET)"
        if now > close_time:
            return False, f"After regular close ({close_time.strftime('%H:%M')} ET)"
        return True, "Extended hours allowed within session calendar"

    if now < open_time:
        return False, f"Before market open ({open_time.strftime('%H:%M')} ET)"
    if now >= close_time:
        return False, f"After market close ({close_time.strftime('%H:%M')} ET)"

    return True, "Regular market hours"


def resolve_expiration_date(expiration: str, reference: datetime | None = None) -> str:
    """Resolve expiration spec to YYYY-MM-DD."""
    reference = reference or datetime.now(tz=ET)
    normalized = expiration.strip().lower()

    if normalized in {"0dte", "+0 days", "+0 day", "today"}:
        return reference.strftime("%Y-%m-%d")

    if len(normalized) == 10 and normalized[4] == "-":
        return normalized

    raise ValueError(f"Unsupported expiration expression: {expiration}")
