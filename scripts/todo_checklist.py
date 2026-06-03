"""Desktop USER-TODO-CHECKLIST.json — pre-market readiness tracking."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

CHECKLIST_PATH = Path(r"C:\Users\Shiel\Desktop\USER-TODO-CHECKLIST.json")
ET = ZoneInfo("America/New_York")

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
    return out


def _fresh_checklist() -> dict[str, Any]:
    return {
        "items": dict(DEFAULT_ITEMS),
        "updated_at": datetime.now(ET).isoformat(timespec="seconds"),
        "notes": "Auto-created — run Desktop bats to mark items done.",
    }


def save_checklist(data: dict[str, Any]) -> None:
    data["updated_at"] = datetime.now(ET).isoformat(timespec="seconds")
    CHECKLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHECKLIST_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def mark_done(item: str) -> bool:
    if item not in DEFAULT_ITEMS:
        raise ValueError(f"Unknown item: {item}")
    data = load_checklist()
    data["items"][item] = True
    save_checklist(data)
    return True


def incomplete_items() -> list[str]:
    data = load_checklist()
    return [k for k, v in data["items"].items() if not v]


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
