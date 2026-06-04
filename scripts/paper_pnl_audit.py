#!/usr/bin/env python3
"""Paper P&L audit — equity snapshot + optional fill test + Activities proof (Track E)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
RENDER = "https://spy-options-bridge.onrender.com"
DESKTOP_REPORT = Path(r"C:\Users\Shiel\Desktop\PAPER-PNL-AUDIT.txt")
ET = ZoneInfo("America/New_York")


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV_PATH.exists():
        return out
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def alpaca_headers(env: dict[str, str]) -> dict[str, str]:
    return {
        "Apca-Api-Key-Id": env.get("APCA_API_KEY_ID") or env.get("ALPACA_API_KEY", ""),
        "Apca-Api-Secret-Key": env.get("APCA_API_SECRET_KEY") or env.get("ALPACA_SECRET_KEY", ""),
    }


def alpaca_base(env: dict[str, str]) -> str:
    return (env.get("APCA_API_BASE_URL") or "https://paper-api.alpaca.markets").rstrip("/")


def account_snapshot(env: dict[str, str]) -> dict[str, str]:
    h = alpaca_headers(env)
    if not h["Apca-Api-Key-Id"]:
        return {"error": "missing_alpaca_keys"}
    r = httpx.get(f"{alpaca_base(env)}/v2/account", headers=h, timeout=30)
    if not r.is_success:
        return {"error": f"account_http_{r.status_code}"}
    j = r.json()
    return {
        "status": str(j.get("status", "?")),
        "equity": str(j.get("equity", "?")),
        "cash": str(j.get("cash", "?")),
        "buying_power": str(j.get("buying_power", "?")),
        "last_equity": str(j.get("last_equity", "?")),
        "portfolio_value": str(j.get("portfolio_value", "?")),
    }


def latest_spy(env: dict[str, str]) -> float:
    h = alpaca_headers(env)
    if not h["Apca-Api-Key-Id"]:
        return 590.0
    base = alpaca_base(env)
    for url in (
        f"{base}/v2/stocks/SPY/quotes/latest",
        "https://data.alpaca.markets/v2/stocks/SPY/trades/latest",
    ):
        try:
            r = httpx.get(url, headers=h, timeout=20)
            if not r.is_success:
                continue
            j = r.json()
            p = j.get("quote", {}).get("ap") or j.get("trade", {}).get("p")
            if p:
                return float(p)
        except Exception:
            pass
    return 590.0


def recent_mleg_orders(env: dict[str, str], *, limit: int = 8) -> list[dict[str, str]]:
    h = alpaca_headers(env)
    if not h["Apca-Api-Key-Id"]:
        return []
    r = httpx.get(
        f"{alpaca_base(env)}/v2/orders",
        headers=h,
        params={"status": "all", "limit": limit, "direction": "desc"},
        timeout=30,
    )
    if not r.is_success:
        return [{"error": f"orders_http_{r.status_code}"}]
    rows: list[dict[str, str]] = []
    for o in r.json():
        if o.get("order_class") != "mleg":
            continue
        rows.append(
            {
                "id": str(o.get("id", ""))[:12],
                "status": str(o.get("status", "?")),
                "filled_qty": str(o.get("filled_qty", "")),
                "filled_avg_price": str(o.get("filled_avg_price", "")),
                "limit_price": str(o.get("limit_price", "")),
                "submitted_at": str(o.get("submitted_at", ""))[:19],
            }
        )
    return rows


def run_exercise_entry(env: dict[str, str], *, base_url: str, timeout: float) -> dict:
    secret = env.get("WEBHOOK_SECRET", "")
    if not secret:
        return {"success": False, "message": "WEBHOOK_SECRET missing"}
    spy = latest_spy(env)
    body = {
        "webhookSecret": secret,
        "ticker": "SPY",
        "action": "PUT_CREDIT_SPREAD",
        "signalPrice": spy,
        "dteFilter": "weekly",
        "strikeOffsetShort": -5,
        "strikeOffsetLong": -6,
        "quantity": 1,
        "fillMode": "exercise",
    }
    try:
        r = httpx.post(f"{base_url}/exercise/entry", json=body, timeout=timeout)
        data = r.json()
        data["_http"] = r.status_code
        return data
    except Exception as exc:
        return {"success": False, "message": str(exc), "filled": False}


def format_report(
    *,
    before: dict[str, str],
    after: dict[str, str],
    orders: list[dict[str, str]],
    entry: dict | None,
    bridge_health: str,
) -> str:
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    lines = [
        f"PAPER P&L AUDIT | {ts}",
        "=" * 60,
        f"Bridge health: {bridge_health}",
        "",
        "ACCOUNT BEFORE",
    ]
    for k, v in before.items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    if entry is not None:
        lines.append("EXERCISE ENTRY (/exercise/entry)")
        lines.append(f"  success: {entry.get('success')}")
        lines.append(f"  filled: {entry.get('filled')}")
        lines.append(f"  status: {entry.get('status')}")
        lines.append(f"  message: {entry.get('message', '')[:120]}")
        lines.append(f"  order_id: {str(entry.get('order_id', ''))[:12]}")
        lines.append("")
    lines.append("ACCOUNT AFTER")
    for k, v in after.items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    try:
        eq0 = float(before.get("equity", "nan"))
        eq1 = float(after.get("equity", "nan"))
        delta = eq1 - eq0
        lines.append(f"EQUITY DELTA: {delta:+.2f} USD")
    except (TypeError, ValueError):
        lines.append("EQUITY DELTA: (could not compute)")
    lines.append("")
    lines.append("RECENT MLEG ORDERS (newest first)")
    if not orders:
        lines.append("  (none)")
    else:
        for o in orders:
            lines.append(
                f"  {o.get('id')} status={o.get('status')} "
                f"filled_qty={o.get('filled_qty')} avg={o.get('filled_avg_price')} "
                f"limit={o.get('limit_price')}"
            )
    lines.extend(
        [
            "",
            "PROOF CHECKLIST",
            "  [ ] Alpaca APP → Activities shows fill (not just Orders=new)",
            "  [ ] equity or last_equity moved OR filled_avg_price on mleg",
            "  [ ] RTH 9:30-16:00 ET if fill attempt failed",
            "",
            "Track D (0dte hold): NOT started — waiting for fill proof.",
        ]
    )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Paper account P&L audit (Track E)")
    parser.add_argument(
        "--try-entry",
        action="store_true",
        help="POST /exercise/entry between snapshots (RTH recommended)",
    )
    parser.add_argument("--local", action="store_true", help="Use http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--out", type=str, default=str(DESKTOP_REPORT))
    args = parser.parse_args(argv)

    env = load_env()
    base_url = "http://127.0.0.1:8000" if args.local else RENDER

    bridge_health = "unknown"
    try:
        h = httpx.get(f"{base_url}/health", timeout=45)
        if h.is_success:
            j = h.json()
            bridge_health = f"v{j.get('version', '?')} tv_risk={j.get('tv_pause_risk', {}).get('level', '?')}"
        else:
            bridge_health = f"HTTP {h.status_code}"
    except Exception as exc:
        bridge_health = f"FAIL {type(exc).__name__}"

    before = account_snapshot(env)
    entry = None
    if args.try_entry:
        entry = run_exercise_entry(env, base_url=base_url, timeout=args.timeout)
        time.sleep(2)
    after = account_snapshot(env)
    orders = recent_mleg_orders(env)

    report = format_report(
        before=before,
        after=after,
        orders=orders,
        entry=entry,
        bridge_health=bridge_health,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"WROTE {out_path}")

    ok_account = "error" not in before and "error" not in after
    ok_fill = entry is None or entry.get("filled")
    return 0 if ok_account and ok_fill else 1


if __name__ == "__main__":
    raise SystemExit(main())
