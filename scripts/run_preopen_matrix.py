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
        note = " | ".join(tail)[:240]
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


def main() -> int:
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    env = load_env()

    # A
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
    post_json(
        "B",
        "POST /exercise/burst count=3",
        "/exercise/burst?count=3&interval=1",
        {**body_base, "fillMode": "exercise", "burstCount": 3, "skipExits": True},
        timeout=600,
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
    run_cmd("D", "prep_market_open.py", ["scripts/prep_market_open.py", "--skip-warning"], timeout=400)
    run_cmd("D", "burst_paper_fills.py --count 3", ["scripts/burst_paper_fills.py", "--count", "3", "--interval", "2"], timeout=900)
    for bat in ("PREP-MARKET-OPEN.bat", "BURST-PAPER-100.bat"):
        p = Path(r"C:\Users\Shiel\Desktop") / bat
        add("D", f"Desktop {bat}", "PASS" if p.exists() else "FAIL", str(p))

    # F
    try:
        hr = httpx.get(f"{RENDER}/health", timeout=20).json()
        ver = str(hr.get("version", "?"))
        add("F", "Render deploy version", "PASS" if ver >= "5.5.8" else "WARN", f"live={ver}")
    except Exception as exc:
        add("F", "Render deploy version", "FAIL", str(exc)[:200])

    lines = [
        "SPY OPTIONS BRIDGE — PRE-OPEN TEST MATRIX",
        f"Generated: {ts}",
        "No secrets in this file.",
        "",
        f"{'Test':<50} {'Result':<8} Notes",
        "-" * 100,
    ]
    for test, status, note in rows:
        lines.append(f"{test:<50} {status:<8} {note}")
    pass_n = sum(1 for _, s, _ in rows if s == "PASS")
    fail_n = sum(1 for _, s, _ in rows if s == "FAIL")
    lines.extend(["", f"SUMMARY: {pass_n} PASS, {fail_n} FAIL, {len(rows) - pass_n - fail_n} other"])
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")
    print(f"SUMMARY: {pass_n} PASS, {fail_n} FAIL")
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
