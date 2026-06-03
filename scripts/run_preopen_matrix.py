#!/usr/bin/env python3
"""Run pre-open validation matrix; write Desktop results (no secrets)."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

from security_utils import redact_line  # noqa: E402
ENV = ROOT / ".env"
RENDER = "https://spy-options-bridge.onrender.com"
OUT = Path(r"C:\Users\Shiel\Desktop\PRE-OPEN-TEST-RESULTS.txt")
ET = ZoneInfo("America/New_York")
rows: list[tuple[str, str, str]] = []


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV.exists():
        return out
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def secret(env: dict[str, str]) -> str:
    s = env.get("WEBHOOK_SECRET", "")
    if not s:
        raise RuntimeError("WEBHOOK_SECRET missing in .env")
    return s


def add(matrix: str, test: str, status: str, note: str = "") -> None:
    rows.append((f"{matrix} | {test}", status, note))


def market_session_open(now: datetime | None = None) -> bool:
    """True Mon–Fri 9:30–16:00 ET (paper fill proof window)."""
    now = now or datetime.now(ET)
    if now.weekday() >= 5:
        return False
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_t <= now < close_t


def run_cmd(matrix: str, test: str, args: list[str], timeout: float = 300) -> None:
    try:
        p = subprocess.run(
            [str(ROOT / ".venv" / "Scripts" / "python.exe"), *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (p.stdout or "") + (p.stderr or "")
        tail = out.strip().splitlines()[-3:] if out.strip() else ["(no output)"]
        note = " | ".join(redact_line(ln) for ln in tail)[:240]
        add(matrix, test, "PASS" if p.returncode == 0 else "FAIL", note)
    except Exception as exc:
        add(matrix, test, "FAIL", str(exc)[:200])


def post_json(matrix: str, test: str, path: str, body: dict, timeout: float = 180) -> None:
    try:
        r = httpx.post(f"{RENDER}{path}", json=body, timeout=timeout)
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        ok = r.is_success and (data.get("success") is not False or r.status_code == 200)
        msg = str(data.get("message") or data.get("status") or r.status_code)[:180]
        filled = data.get("filled") or data.get("filled_count")
        if filled is not None:
            msg += f" filled={filled}"
        add(matrix, test, "PASS" if ok else "FAIL", msg)
    except Exception as exc:
        add(matrix, test, "FAIL", str(exc)[:200])


def alpaca_checks(env: dict[str, str]) -> None:
    key = env.get("APCA_API_KEY_ID") or env.get("ALPACA_API_KEY", "")
    sec = env.get("APCA_API_SECRET_KEY") or env.get("ALPACA_SECRET_KEY", "")
    if not key or not sec:
        add("C", "Account API", "FAIL", "Alpaca keys missing in .env")
        add("C", "Recent mleg orders", "SKIP", "no keys")
        return
    base = (env.get("APCA_API_BASE_URL") or "https://paper-api.alpaca.markets").rstrip("/")
    h = {"Apca-Api-Key-Id": key, "Apca-Api-Secret-Key": sec}
    try:
        ar = httpx.get(f"{base}/v2/account", headers=h, timeout=30)
        if ar.is_success:
            acct = ar.json()
            add(
                "C",
                "Account API",
                "PASS",
                f"status={acct.get('status')} equity={acct.get('equity')} buying_power={acct.get('buying_power')}",
            )
        else:
            add("C", "Account API", "FAIL", f"HTTP {ar.status_code}")
    except Exception as exc:
        add("C", "Account API", "FAIL", str(exc)[:200])

    try:
        orr = httpx.get(
            f"{base}/v2/orders",
            headers=h,
            params={"status": "all", "limit": 15, "nested": "true"},
            timeout=30,
        )
        if not orr.is_success:
            add("C", "Recent mleg orders", "FAIL", f"HTTP {orr.status_code}")
            return
        mlegs = [o for o in orr.json() if o.get("order_class") == "mleg"][:5]
        if not mlegs:
            add("C", "Recent mleg orders", "PASS", "no mleg rows (or none recent)")
            return
        latest = mlegs[0]
        add(
            "C",
            "Recent mleg orders",
            "PASS",
            f"latest status={latest.get('status')} limit={latest.get('limit_price')} created={(latest.get('created_at') or '')[:19]}",
        )
    except Exception as exc:
        add("C", "Recent mleg orders", "FAIL", str(exc)[:200])


def render_get(matrix: str, test: str, path: str) -> None:
    try:
        r = httpx.get(f"{RENDER}{path}", timeout=30)
        data = r.json()
        if path == "/health":
            note = (
                f"version={data.get('version')} configured={data.get('configured')} "
                f"paper_force={data.get('paper_force_min_fill')} entry_min_credit={data.get('entry_min_credit')}"
            )
        else:
            note = json.dumps(data)[:180]
        add(matrix, test, "PASS" if r.is_success else "FAIL", note)
    except Exception as exc:
        add(matrix, test, "FAIL", str(exc)[:200])


DESKTOP_BATS = (
    "SETUP-EMAIL-AUTOMATION.bat",
    "TEST-EMAIL-NOW.bat",
    "CONFIRM-RENDER-EMAIL.bat",
    "BURST-PAPER-100.bat",
    "BRIDGE-KEEPALIVE.bat",
    "DUAL-SYNC-LOOP.bat",
    "PREP-MARKET-OPEN.bat",
    "RUN-THURSDAY-LIVE.bat",
    "REMIND-BEFORE-OPEN.bat",
    "SCHEDULE-9AM-REMINDER.bat",
    "OPEN-ALL-REPORTS.bat",
    "START-REDUNDANT-TEST-LOOP.bat",
    "STOP-REDUNDANT-TESTS.bat",
)


def run_matrix(*, fast: bool = False) -> tuple[int, int, list[tuple[str, str, str]]]:
    """Run validation matrix; return (pass_count, fail_count, rows)."""
    global rows
    rows = []
    env = load_env()

    if fast:
        run_cmd("A", "pytest quick", ["-m", "pytest", "tests/", "-q", "--tb=no", "-x"], timeout=90)
    else:
        run_cmd("A", "pytest full suite", ["-m", "pytest", "tests/", "-q", "--tb=no"], timeout=120)
    try:
        import main as m  # noqa: F401

        add("A", "main.py import smoke", "PASS", "import ok")
    except Exception as exc:
        add("A", "main.py import smoke", "FAIL", str(exc)[:200])

    # B
    render_get("B", "GET /health", "/health")
    render_get("B", "GET /ping", "/ping")

    body_base = {
        "webhookSecret": secret(env),
        "ticker": "SPY",
        "action": "PUT_CREDIT_SPREAD",
        "signalPrice": 590.0,
        "dteFilter": "weekly",
        "strikeOffsetShort": -5,
        "strikeOffsetLong": -6,
        "quantity": 1,
    }
    post_json("B", "POST /entry", "/entry", {**body_base, "limitCredit": 0.55, "fillMode": "aggressive"})
    post_json("B", "POST /exercise/entry", "/exercise/entry", {**body_base, "fillMode": "exercise"})
    burst_n = 1 if fast else 3
    post_json(
        "B",
        f"POST /exercise/burst count={burst_n}",
        f"/exercise/burst?count={burst_n}&interval=1",
        {**body_base, "fillMode": "exercise", "burstCount": burst_n, "skipExits": True},
        timeout=300 if fast else 600,
    )
    post_json(
        "B",
        "POST /warning overrideAutoClose",
        "/warning",
        {
            "webhookSecret": secret(env),
            "ticker": "SPY",
            "signalPrice": 590.0,
            "strikeOffsetShort": -5,
            "strikeOffsetLong": -6,
            "overrideAutoClose": True,
        },
    )

    # C
    alpaca_checks(env)

    # D
    if not fast:
        if market_session_open():
            run_cmd("D", "prep_market_open.py", ["scripts/prep_market_open.py", "--skip-warning"], timeout=400)
            run_cmd(
                "D",
                "burst_paper_fills.py --count 3",
                ["scripts/burst_paper_fills.py", "--count", "3", "--interval", "2"],
                timeout=900,
            )
        else:
            add(
                "D",
                "prep_market_open.py",
                "LIMIT",
                "After hours — re-run 9:30–16:00 ET for fill proof",
            )
            add(
                "D",
                "burst_paper_fills.py --count 3",
                "LIMIT",
                "After hours — use BURST-PAPER-100.bat at 9:31 ET",
            )
    for bat in DESKTOP_BATS:
        p = Path(r"C:\Users\Shiel\Desktop") / bat
        add("D", f"Desktop {bat}", "PASS" if p.exists() else "FAIL", str(p))

    # F
    try:
        hr = httpx.get(f"{RENDER}/health", timeout=20).json()
        ver = str(hr.get("version", "?"))
        add("F", "Render deploy version", "PASS" if ver >= "5.5.8" else "WARN", f"live={ver}")
    except Exception as exc:
        add("F", "Render deploy version", "FAIL", str(exc)[:200])

    pass_n = sum(1 for _, s, _ in rows if s == "PASS")
    fail_n = sum(1 for _, s, _ in rows if s == "FAIL")
    return pass_n, fail_n, list(rows)


def format_report(ts: str, pass_n: int, fail_n: int, row_list: list[tuple[str, str, str]]) -> str:
    lines = [
        "SPY OPTIONS BRIDGE — PRE-OPEN TEST MATRIX",
        f"Generated: {ts}",
        "No secrets in this file.",
        "",
        f"{'Test':<50} {'Result':<8} Notes",
        "-" * 100,
    ]
    for test, status, note in row_list:
        lines.append(f"{test:<50} {status:<8} {note}")
    limit_n = sum(1 for _, s, _ in row_list if s == "LIMIT")
    other_n = len(row_list) - pass_n - fail_n - limit_n
    lines.extend(
        [
            "",
            f"SUMMARY: {pass_n} PASS, {fail_n} FAIL, {limit_n} LIMIT, {other_n} other",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="Shorter matrix for redundant loop")
    parser.add_argument("--append", action="store_true", help="Append cycle block to results file")
    args = parser.parse_args()

    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    pass_n, fail_n, row_list = run_matrix(fast=args.fast)
    block = format_report(ts, pass_n, fail_n, row_list)
    if args.append:
        prior = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        OUT.write_text(prior + "\n" + "=" * 80 + f"\nCYCLE {ts}\n" + block, encoding="utf-8")
    else:
        OUT.write_text(block, encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"SUMMARY: {pass_n} PASS, {fail_n} FAIL")
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
