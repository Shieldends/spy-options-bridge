"""Desktop USER-TODO-CHECKLIST.json — pre-market readiness tracking."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

CHECKLIST_PATH = Path(r"C:\Users\Shiel\Desktop\USER-TODO-CHECKLIST.json")
ET = ZoneInfo("America/New_York")
USER_INBOX = "shieldinc850@gmail.com"

USER_PREFS_DEFAULTS: dict[str, Any] = {
    "user_wants_email": True,
    "user_wants_burst": True,
    "priority": ["email_setup", "render_email_env", "burst_931_et"],
}

DEFAULT_ITEMS: dict[str, bool] = {
    "email_setup_done": False,
    "email_test_done": False,
    "render_email_env": False,
    "google_app_password_via_setup_bat_only": False,
    "dual_sync_running": False,
    "keepalive_running": False,
    "tradingview_alerts_confirmed": True,
    "alpaca_old_orders_canceled": True,
}

LABELS: dict[str, str] = {
    "email_setup_done": "Local email .env (SETUP-EMAIL-AUTOMATION.bat)",
    "email_test_done": "Test email sent (TEST-EMAIL-NOW.bat)",
    "render_email_env": "Render dashboard EMAIL_* + Manual Deploy",
    "google_app_password_via_setup_bat_only": (
        "Gmail app password entered ONLY via SETUP-EMAIL-AUTOMATION.bat (never chat)"
    ),
    "dual_sync_running": "Grok dual sync left open (DUAL-SYNC-LOOP.bat)",
    "keepalive_running": "Bridge keepalive left open (BRIDGE-KEEPALIVE.bat)",
    "tradingview_alerts_confirmed": "TradingView SPY MACD Entry + Warning alerts ON",
    "alpaca_old_orders_canceled": "Alpaca paper old orders canceled / account flat",
}

# Shown in GUI "What's left?" only — email/sync items are optional/automated.
USER_LIVE_REQUIRED: tuple[str, ...] = (
    "tradingview_alerts_confirmed",
    "alpaca_old_orders_canceled",
)

BAT_FOR_ITEM: dict[str, str] = {
    "email_setup_done": r"C:\Users\Shiel\Desktop\SETUP-EMAIL-AUTOMATION.bat",
    "google_app_password_via_setup_bat_only": r"C:\Users\Shiel\Desktop\SETUP-EMAIL-AUTOMATION.bat",
    "email_test_done": r"C:\Users\Shiel\Desktop\TEST-EMAIL-NOW.bat",
    "render_email_env": r"C:\Users\Shiel\Desktop\CONFIRM-RENDER-EMAIL.bat",
    "dual_sync_running": r"C:\Users\Shiel\Desktop\DUAL-SYNC-LOOP.bat",
    "keepalive_running": r"C:\Users\Shiel\Desktop\BRIDGE-KEEPALIVE.bat",
}


def load_checklist() -> dict[str, Any]:
    if not CHECKLIST_PATH.exists():
        return _fresh_checklist()
    try:
        data = json.loads(CHECKLIST_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _fresh_checklist()
    raw_items = data.get("items") if isinstance(data.get("items"), dict) else data
    items = dict(DEFAULT_ITEMS)
    for key in DEFAULT_ITEMS:
        if key in raw_items:
            items[key] = bool(raw_items[key])
    out: dict[str, Any] = {
        "items": items,
        "updated_at": data.get("updated_at", ""),
        "notes": data.get("notes", ""),
    }
    for key, default in USER_PREFS_DEFAULTS.items():
        out[key] = data.get(key, default)
    return out


def _fresh_checklist() -> dict[str, Any]:
    return {
        "items": dict(DEFAULT_ITEMS),
        "updated_at": datetime.now(ET).isoformat(timespec="seconds"),
        "notes": "User confirmed email + burst — reply YES/NO to team emails.",
        **USER_PREFS_DEFAULTS,
    }


def user_wants_email() -> bool:
    return bool(load_checklist().get("user_wants_email", True))


def user_wants_burst() -> bool:
    return bool(load_checklist().get("user_wants_burst", True))


def set_user_prefs(*, email: bool | None = None, burst: bool | None = None) -> None:
    data = load_checklist()
    if email is not None:
        data["user_wants_email"] = bool(email)
    if burst is not None:
        data["user_wants_burst"] = bool(burst)
    save_checklist(data)


def save_checklist(data: dict[str, Any]) -> None:
    data["updated_at"] = datetime.now(ET).isoformat(timespec="seconds")
    CHECKLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHECKLIST_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def mark_done(item: str) -> bool:
    return set_item(item, True)


def set_item(item: str, done: bool) -> bool:
    if item not in DEFAULT_ITEMS:
        raise ValueError(f"Unknown item: {item}")
    data = load_checklist()
    data["items"][item] = bool(done)
    save_checklist(data)
    return bool(done)


def toggle_item(item: str) -> bool:
    data = load_checklist()
    new_val = not bool(data["items"][item])
    data["items"][item] = new_val
    save_checklist(data)
    return new_val


def incomplete_items() -> list[str]:
    data = load_checklist()
    return [k for k, v in data["items"].items() if not v]


def ensure_live_defaults() -> None:
    """Pre-market: assume TV alerts + flat Alpaca unless user unchecked."""
    data = load_checklist()
    changed = False
    for key in USER_LIVE_REQUIRED:
        if not data["items"].get(key):
            data["items"][key] = True
            changed = True
    if changed:
        save_checklist(data)


def format_user_live_lines(*, team_running: bool, max_lines: int = 3) -> list[str]:
    """Human-required only — max 3 lines for popups."""
    lines: list[str] = []
    data = load_checklist()
    if not team_running:
        lines.append("Click START TEAM (or leave SPY-LIVE-COMMAND open overnight)")
    if data.get("user_wants_email", True) and not data["items"].get("email_setup_done"):
        lines.append("Email: GUI app password + Save → CONFIRM-RENDER-EMAIL.bat once")
    elif data.get("user_wants_email", True) and not data["items"].get("render_email_env"):
        lines.append("Render: CONFIRM-RENDER-EMAIL.bat (one click after dashboard SMTP)")
    if data.get("user_wants_burst", True) and len(lines) < max_lines:
        lines.append("Burst: click THURSDAY BURST at 9:31 ET")
    for key in USER_LIVE_REQUIRED:
        if len(lines) >= max_lines:
            return lines[:max_lines]
        if not data["items"].get(key):
            lines.append(LABELS.get(key, key))
    return lines[:max_lines]


def format_incomplete_lines() -> list[str]:
    lines: list[str] = []
    for key in incomplete_items():
        label = LABELS.get(key, key)
        bat = BAT_FOR_ITEM.get(key, "")
        line = f"- [ ] {label}"
        if bat:
            line += f"  → {bat}"
        lines.append(line)
    return lines


def is_weekday_market_day(now: datetime | None = None) -> bool:
    now = now or datetime.now(ET)
    return now.weekday() < 5


def before_market_open(now: datetime | None = None) -> bool:
    now = now or datetime.now(ET)
    if not is_weekday_market_day(now):
        return False
    open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
    return now < open_time


def before_reminder_window_end(now: datetime | None = None) -> bool:
    """True between midnight and 9:30 ET on a weekday (reminder window)."""
    return before_market_open(now)


def write_human_summary(path: Path | None = None) -> Path:
    """Mirror checklist to Desktop USER-TODO-CHECKLIST.txt for Notepad."""
    out = path or CHECKLIST_PATH.with_suffix(".txt")
    data = load_checklist()
    now = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    lines = [
        "USER TODO CHECKLIST (mirror of USER-TODO-CHECKLIST.json)",
        f"Updated: {now}",
        "",
    ]
    for key, done in data["items"].items():
        mark = "x" if done else " "
        lines.append(f"[{mark}] {LABELS.get(key, key)}")
    missing = incomplete_items()
    lines.extend(
        [
            "",
            f"Incomplete: {len(missing)}",
            "Run REMIND-BEFORE-OPEN.bat before 9:30 ET for email reminder.",
        ]
    )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
