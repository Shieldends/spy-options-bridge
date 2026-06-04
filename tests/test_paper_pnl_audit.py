"""paper_pnl_audit report formatting."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import paper_pnl_audit as audit  # noqa: E402


def test_format_report_equity_delta():
    text = audit.format_report(
        before={"equity": "10000.00", "cash": "10000"},
        after={"equity": "10015.50", "cash": "10015.5"},
        orders=[{"id": "abc", "status": "filled", "filled_qty": "1", "filled_avg_price": "-0.45", "limit_price": "-0.01", "submitted_at": ""}],
        entry={"success": True, "filled": True, "status": "filled", "message": "ok", "order_id": "abc"},
        bridge_health="v5.5.9 green",
    )
    assert "EQUITY DELTA: +15.50 USD" in text
    assert "filled" in text
    assert "Track D" in text
