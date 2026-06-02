from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from spy_options_bridge.guards.market_hours import ET, is_regular_market_hours


@pytest.mark.parametrize(
    "hour,minute,expected_open",
    [
        (9, 29, False),
        (10, 0, True),
        (15, 59, True),
        (16, 0, False),
    ],
)
def test_market_hours_on_weekday(hour, minute, expected_open):
    # Use a known NYSE session date (not a holiday)
    dt = datetime(2026, 5, 29, hour, minute, tzinfo=ET)
    is_open, _ = is_regular_market_hours(dt)
    assert is_open is expected_open


def test_market_closed_on_weekend():
    dt = datetime(2026, 5, 30, 12, 0, tzinfo=ET)  # Saturday
    is_open, reason = is_regular_market_hours(dt)
    assert is_open is False
    assert "session" in reason.lower()
