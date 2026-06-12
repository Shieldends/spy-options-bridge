#!/usr/bin/env python3
"""One-page spread session digest — bridge webhooks + Alpaca orders/fills (today ET)."""

from __future__ import annotations

import argparse
import html
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from paper_pnl_audit import account_snapshot, alpaca_base, alpaca_headers, load_env  # noqa: E402

ET = ZoneInfo("America/New_York")
RENDER = "https://spy-options-bridge.onrender.com"
DESKTOP = Path(r"C:\Users\Shiel\Desktop")
OUT_TXT = DESKTOP / "SPREAD-ACTIVITY-DIGEST.txt"
OUT_HTML = DESKTOP / "SPREAD-ACTIVITY-DIGEST.html"
OUT_SIMPLE = DESKTOP / "SPREAD-TODAY-SIMPLE.txt"


def today_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


def utc_to_et_label(ts: str) -> str:
    raw = (ts or "").replace("Z", "+00:00")[:25]
    if not raw:
        return "??:??:?? ET"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            from datetime import timezone

            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ET).strftime("%H:%M:%S ET")
    except Exception:
        return raw[:19]


def fetch_bridge_activity() -> tuple[dict, list[dict]]:
    try:
        r = httpx.get(f"{RENDER}/activity", timeout=35.0)
        if r.is_success:
            data = r.json()
            return data, list(data.get("events") or [])
    except Exception as exc:
        return {"error": str(exc), "version": "?"}, []
    return {"error": f"http_{r.status_code}"}, []


def fetch_bridge_health() -> dict:
    try:
        r = httpx.get(f"{RENDER}/health", timeout=35.0)
        return r.json() if r.is_success else {"error": f"http_{r.status_code}"}
    except Exception as exc:
        return {"error": str(exc)}


def classify_order(o: dict) -> str:
    oc = str(o.get("order_class") or "").lower()
    sym = str(o.get("symbol") or "")
    legs = o.get("legs") or []
    lim = str(o.get("limit_price") or "")
    if (oc == "mleg" or legs) and lim == "-0.05" and str(o.get("status")) == "canceled":
        return "BURST-TEST"
    if oc == "mleg" or legs:
        return "SPREAD"
    if sym.startswith("SPY") and len(sym) > 10:
        return "SINGLE-PUT"
    if not sym:
        return "SPREAD-ATTEMPT"
    return "OTHER"


def fetch_alpaca_today(env: dict[str, str]) -> dict[str, list]:
    h = alpaca_headers(env)
    if not h["Apca-Api-Key-Id"]:
        return {"orders": [], "fills": [], "positions": [], "open": []}
    base = alpaca_base(env)
    day = today_et()
    out: dict[str, list] = {"orders": [], "fills": [], "positions": [], "open": []}
    with httpx.Client(timeout=35.0) as client:
        r = client.get(
            f"{base}/v2/orders",
            headers=h,
            params={"status": "all", "limit": 200, "direction": "desc", "nested": "true"},
        )
        if r.is_success:
            for o in r.json():
                created = str(o.get("created_at") or "")
                if utc_to_et_label(created).startswith("??"):
                    continue
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(ET)
                except Exception:
                    continue
                if dt.strftime("%Y-%m-%d") != day:
                    continue
                kind = classify_order(o)
                legs_n = len(o.get("legs") or [])
                out["orders"].append(
                    {
                        "time": dt.strftime("%H:%M:%S ET"),
                        "sort": dt,
                        "source": "alpaca",
                        "kind": kind,
                        "outcome": str(o.get("status") or "?"),
                        "message": (
                            f"{kind} {o.get('side','')} "
                            f"limit={o.get('limit_price')} filled={o.get('filled_qty')} "
                            f"sym={(o.get('symbol') or f'{legs_n}-leg')[:22]}"
                        ),
                    }
                )

        r2 = client.get(
            f"{base}/v2/account/activities/FILL",
            headers=h,
            params={"direction": "desc", "page_size": 100},
        )
        if r2.is_success:
            for f in r2.json():
                ts = str(f.get("transaction_time") or "")
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ET)
                except Exception:
                    continue
                if dt.strftime("%Y-%m-%d") != day:
                    continue
                sym = str(f.get("symbol") or "")
                out["fills"].append(
                    {
                        "time": dt.strftime("%H:%M:%S ET"),
                        "sort": dt,
                        "source": "alpaca-fill",
                        "kind": "FILL",
                        "outcome": "filled",
                        "message": f"{f.get('side')} {sym} qty={f.get('qty')} @ ${f.get('price')}",
                    }
                )

        r3 = client.get(f"{base}/v2/orders", headers=h, params={"status": "open", "limit": 50})
        if r3.is_success:
            out["open"] = r3.json()

        r4 = client.get(f"{base}/v2/positions", headers=h)
        if r4.is_success:
            out["positions"] = r4.json()
    return out


def bridge_rows(events: list[dict]) -> list[dict]:
    rows = []
    for e in events:
        ts = str(e.get("ts_et") or "")
        try:
            sort = datetime.strptime(ts.replace(" ET", ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=ET)
        except Exception:
            sort = datetime.now(ET)
        rows.append(
            {
                "time": ts[-11:] if " ET" in ts else ts,
                "sort": sort,
                "source": "bridge",
                "kind": str(e.get("kind") or "?").upper(),
                "outcome": str(e.get("outcome") or "?"),
                "message": str(e.get("message") or ""),
            }
        )
    return rows


def summarize(timeline: list[dict], bridge_meta: dict, health: dict, acc: dict, alpaca: dict) -> list[str]:
    bridge_ev = [t for t in timeline if t["source"] == "bridge"]
    spread_ev = [t for t in timeline if t["kind"] == "SPREAD"]
    burst_ev = [t for t in timeline if t["kind"] == "BURST-TEST"]
    single_ev = [t for t in timeline if t["kind"] == "SINGLE-PUT"]
    warn_ev = [t for t in timeline if t["kind"] == "WARNING"]
    skipped = [t for t in timeline if t["outcome"] in ("skipped", "failed", "rejected")]
    fills = [t for t in timeline if t["outcome"] == "filled" or t["source"] == "alpaca-fill"]
    lines = [
        "TODAY AT A GLANCE",
        f"  Bridge version: {health.get('version', bridge_meta.get('version', '?'))}",
        f"  Bridge webhook log rows: {bridge_meta.get('count', len(bridge_ev))}",
        f"  Spread orders (mleg): {len(spread_ev)}",
        f"  Burst test cancels (9:31 noise): {len(burst_ev)}",
        f"  Single-put orders (old strategy): {len(single_ev)}",
        f"  Warning webhooks: {len(warn_ev)}",
        f"  Skipped / failed / rejected: {len(skipped)}",
        f"  Fills today: {len(fills)}",
        f"  Open orders now: {len(alpaca.get('open') or [])}",
        f"  Positions now: {len(alpaca.get('positions') or [])}",
        f"  Equity: {acc.get('equity', '?')}",
    ]
    if bridge_meta.get("error"):
        lines.append(f"  Bridge /activity: {bridge_meta['error']} (deploy 5.5.14+ for webhook log)")
    elif bridge_meta.get("note"):
        lines.append(f"  Note: {bridge_meta['note']}")
    return lines


def build_timeline(bridge_events: list[dict], alpaca: dict) -> list[dict]:
    rows = bridge_rows(bridge_events)
    rows.extend(alpaca.get("orders") or [])
    rows.extend(alpaca.get("fills") or [])
    rows.sort(key=lambda r: r["sort"])
    return rows


def filter_timeline(timeline: list[dict], *, hide_burst: bool = True) -> list[dict]:
    if not hide_burst:
        return timeline
    return [t for t in timeline if t["kind"] != "BURST-TEST"]


def render_plain_english(
    timeline: list[dict],
    health: dict,
    acc: dict,
    alpaca: dict,
) -> str:
    """Short summary Commander can read in 30 seconds."""
    burst_n = sum(1 for t in timeline if t["kind"] == "BURST-TEST")
    single_n = sum(1 for t in timeline if t["kind"] == "SINGLE-PUT")
    spread_n = sum(1 for t in timeline if t["kind"] == "SPREAD")
    bridge_rows_ = [t for t in timeline if t["source"] == "bridge"]
    warn_n = sum(1 for t in bridge_rows_ if t["kind"] == "WARNING")
    entry_bridge = [t for t in bridge_rows_ if t["kind"] == "ENTRY"]
    skipped = [t for t in entry_bridge if t["outcome"] in ("skipped", "failed", "rejected")]
    spread_fills = [t for t in timeline if t["kind"] == "SPREAD" and t["outcome"] == "filled"]
    pos_n = len(alpaca.get("positions") or [])
    now = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")

    lines = [
        "SPY SPREAD - TODAY IN PLAIN ENGLISH",
        f"Updated: {now}",
        "",
        "BOTTOM LINE",
    ]
    if spread_fills:
        lines.append(f"  You have {len(spread_fills)} bull put SPREAD fill(s) today.")
    else:
        lines.append("  No bull put SPREAD fills today.")
    if pos_n:
        lines.append(f"  Open positions now: {pos_n}")
    else:
        lines.append("  Account is FLAT right now.")
    lines.append(f"  Paper equity: ${acc.get('equity', '?')}")
    lines.append("")
    lines.append("WHAT MATTERS FOR YOUR NEW STRATEGY")
    if entry_bridge:
        lines.append(f"  TV entry webhooks received: {len(entry_bridge)}")
        for t in entry_bridge[-5:]:
            lines.append(f"    {t['time']} -> {t['outcome']}: {t['message'][:70]}")
    else:
        lines.append("  No spread entry webhooks logged yet (or before bridge 5.5.14).")
    if warn_n:
        lines.append(f"  Warning webhooks: {warn_n}")
    lines.append("")
    lines.append("NOISE YOU CAN IGNORE")
    lines.append(f"  {burst_n} burst-test cancels at 9:31 AM (automated junk - not you).")
    lines.append(f"  {single_n} old SINGLE-PUT orders (previous strategy - not spread).")
    lines.append("")
    lines.append("BRIDGE")
    lines.append(f"  Version {health.get('version', '?')} | status {health.get('status', '?')}")
    lines.append("")
    lines.append("MONDAY: press G before open, unpause TV alerts, press A anytime.")
    lines.append("Full detail (optional): SPREAD-ACTIVITY-DIGEST.txt on Desktop")
    return "\n".join(lines) + "\n"


def render_txt(summary: list[str], timeline: list[dict], health: dict) -> str:
    now = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    parts = [
        "SPY BULL PUT SPREAD - ACTIVITY DIGEST",
        f"Generated: {now}",
        "=" * 60,
        "",
        "HOW TO READ THIS",
        "  TV 'Delivered' = webhook reached Render.",
        "  bridge / skipped / failed = no Alpaca spread (often credit below $0.40).",
        "  SPREAD + filled = real multi-leg order on Alpaca.",
        "  SINGLE-PUT = old naked-put path (not spread).",
        "  BURST-TEST = 9:31 exercise spam (ignore).",
        "",
        *summary,
        "",
        "BRIDGE HEALTH",
        f"  status={health.get('status')} tv_risk={health.get('tv_pause_risk', {}).get('level')}",
        f"  open_orders={health.get('open_order_count')} open_mleg={health.get('open_mleg_count')}",
        "",
        f"BURST noise hidden ({sum(1 for t in timeline if t['kind']=='BURST-TEST')} rows) - see SIMPLE file",
        "",
        "TIMELINE - spread + bridge only (oldest to newest)",
        "-" * 60,
    ]
    shown = filter_timeline(timeline)
    if not shown:
        parts.append("  (no spread/bridge events today)")
    for t in shown:
        parts.append(
            f"  {t['time']}  [{t['source']}]  {t['kind']}/{t['outcome']}: {t['message']}"
        )
    parts.extend(["", "=" * 60, "Refresh: Commander A or double-click SPREAD-ACTIVITY.bat"])
    return "\n".join(parts) + "\n"


def render_html(summary: list[str], timeline: list[dict], health: dict) -> str:
    now = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    tv_level = str((health.get("tv_pause_risk") or {}).get("level", "?"))
    bridge_ver = str(health.get("version", "?"))

    def row_color(outcome: str) -> str:
        if outcome in ("filled", "accepted"):
            return "#d4edda"
        if outcome in ("skipped", "failed", "rejected", "no_position"):
            return "#fff3cd"
        if outcome == "no_danger":
            return "#e8f4fc"
        return "#ffffff"

    trs = []
    for t in timeline:
        trs.append(
            f"<tr style='background:{row_color(t['outcome'])}'>"
            f"<td>{html.escape(t['time'])}</td>"
            f"<td>{html.escape(t['source'])}</td>"
            f"<td><b>{html.escape(t['kind'])}</b></td>"
            f"<td>{html.escape(t['outcome'])}</td>"
            f"<td>{html.escape(t['message'])}</td></tr>"
        )
    summary_li = "".join(f"<li>{html.escape(s)}</li>" for s in summary)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Spread Activity</title>
<style>
body {{ font-family: Segoe UI, Arial; font-size: 18px; margin: 24px; max-width: 1100px; }}
h1 {{ font-size: 28px; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
th, td {{ border: 1px solid #ccc; padding: 10px; text-align: left; vertical-align: top; }}
th {{ background: #1a3a5c; color: #fff; font-size: 16px; }}
.legend {{ background: #f5f5f5; padding: 14px; border-radius: 8px; }}
</style></head><body>
<h1>SPY Spread Activity — {html.escape(now)}</h1>
<div class="legend">
<p><b>TV Delivered</b> = webhook hit Render. <b>skipped/failed</b> = protected, no spread order.
<b>SPREAD filled</b> = Alpaca multi-leg. <b>SINGLE-PUT</b> = old naked put.</p>
</div>
<ul>{summary_li}</ul>
<p>Bridge: v{html.escape(bridge_ver)} | tv_risk={html.escape(tv_level)}</p>
<table>
<tr><th>Time</th><th>Source</th><th>Type</th><th>Result</th><th>Detail</th></tr>
{''.join(trs) if trs else "<tr><td colspan=5>No events today</td></tr>"}
</table>
<p><i>Press F5 to refresh after running Commander A.</i></p>
</body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-open", action="store_true", help="Write files only")
    args = parser.parse_args()

    env = load_env()
    bridge_meta, bridge_events = fetch_bridge_activity()
    health = fetch_bridge_health()
    acc = account_snapshot(env)
    alpaca = fetch_alpaca_today(env)
    timeline = build_timeline(bridge_events, alpaca)
    summary = summarize(timeline, bridge_meta, health, acc, alpaca)
    simple = render_plain_english(timeline, health, acc, alpaca)

    DESKTOP.mkdir(parents=True, exist_ok=True)
    OUT_SIMPLE.write_text(simple, encoding="utf-8")
    txt = render_txt(summary, timeline, health)
    OUT_TXT.write_text(txt, encoding="utf-8")
    OUT_HTML.write_text(render_html(summary, filter_timeline(timeline), health), encoding="utf-8")

    print(txt)
    print(f"Wrote {OUT_TXT}")
    print(f"Wrote {OUT_HTML}")

    if not args.no_open:
        import os

        os.startfile(str(OUT_HTML))  # type: ignore[attr-defined]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
