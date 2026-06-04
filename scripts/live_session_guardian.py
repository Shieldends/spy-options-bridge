#!/usr/bin/env python3
"""
Keep live MACD path healthy until market close — headless when GUI fails.

Runs every 5 min (RTH): Render /health, Alpaca account, core workers, journal.
Does NOT place trades. Restarts dual_sync + keepalive only when down.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import command_center as cc  # noqa: E402
import dedupe_spy_workers as dedupe  # noqa: E402

from run_preopen_matrix import market_session_open  # noqa: E402

ET = ZoneInfo("America/New_York")
RENDER_HEALTH = "https://spy-options-bridge.onrender.com/health"
DESKTOP = Path(r"C:\Users\Shiel\Desktop")
ENV_PATH = ROOT / ".env"
DEFAULT_INTERVAL = 300


def journal_path(day: datetime | None = None) -> Path:
    d = (day or datetime.now(ET)).strftime("%Y-%m-%d")
    return DESKTOP / f"LIVE-SESSION-JOURNAL-{d}.txt"


def log(msg: str) -> None:
    ts = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
    line = f"{ts} {msg}"
    print(line)
    path = journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


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


def bridge_health() -> dict[str, str]:
    try:
        r = httpx.get(RENDER_HEALTH, timeout=45)
        if not r.is_success:
            return {"ok": "false", "http": str(r.status_code)}
        j = r.json()
        risk = j.get("tv_pause_risk") or {}
        return {
            "ok": "true",
            "version": str(j.get("version", "?")),
            "tv_risk": str(risk.get("level", "?")),
            "open_mleg": str(risk.get("open_mleg_count", j.get("open_mleg_count", "?"))),
        }
    except Exception as exc:
        return {"ok": "false", "error": type(exc).__name__}


def alpaca_line(env: dict[str, str]) -> str:
    key = env.get("APCA_API_KEY_ID") or env.get("ALPACA_API_KEY", "")
    if not key:
        return "alpaca=not_configured"
    base = (env.get("APCA_API_BASE_URL") or "https://paper-api.alpaca.markets").rstrip("/")
    h = {
        "Apca-Api-Key-Id": key,
        "Apca-Api-Secret-Key": env.get("APCA_API_SECRET_KEY") or env.get("ALPACA_SECRET_KEY", ""),
    }
    try:
        acc = httpx.get(f"{base}/v2/account", headers=h, timeout=30)
        pos = httpx.get(f"{base}/v2/positions", headers=h, timeout=30)
        if not acc.is_success:
            return f"alpaca account HTTP {acc.status_code}"
        a = acc.json()
        npos = len(pos.json()) if pos.is_success else -1
        return (
            f"alpaca equity={a.get('equity')} cash={a.get('cash')} "
            f"positions={npos} last_equity={a.get('last_equity')}"
        )
    except Exception as exc:
        return f"alpaca error={type(exc).__name__}"


def worker_line() -> str:
    status = cc.team_worker_status()
    counts = {s: (1 if status.get(s) else 0) for s in cc.TEAM_WORKER_SCRIPTS}
    dupes = [k for k, v in counts.items() if v > 1]
    core = status.get("dual_sync_loop.py") and status.get("bridge_keepalive.py")
    note = " READY" if core and not dupes else " WARN"
    if dupes:
        note += f" dupes={','.join(dupes)}"
    return f"workers {counts}{note}"


def ensure_core_workers() -> list[str]:
    """Start missing core helpers only; never redundant during RTH."""
    actions: list[str] = []
    cc.stop_stale_console_supervisors()
    for name, script, args in cc.WORKERS:
        if not cc.should_spawn_worker(name, script):
            continue
        if cc.worker_already_running(script):
            continue
        cc.spawn_worker(name, script, args)
        actions.append(f"spawned {name}")
    if any(cc.team_worker_status().values()):
        cc.dedupe_worker_duplicates_only()
        actions.append("dedupe pass")
    return actions


def one_cycle(*, repair: bool) -> None:
    env = load_env()
    h = bridge_health()
    log(f"GUARDIAN | bridge {json.dumps(h, sort_keys=True)}")
    log(f"GUARDIAN | {alpaca_line(env)}")
    log(f"GUARDIAN | {worker_line()}")
    if repair:
        acts = ensure_core_workers()
        if acts:
            log(f"GUARDIAN | repair {'; '.join(acts)}")
        else:
            log("GUARDIAN | repair nothing needed")


def session_active() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    start = now.replace(hour=9, minute=25, second=0, microsecond=0)
    end = now.replace(hour=16, minute=5, second=0, microsecond=0)
    return start <= now < end


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live session guardian until market close")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    parser.add_argument("--once", action="store_true", help="Single check (for bat / task)")
    parser.add_argument("--no-repair", action="store_true", help="Log only, do not spawn workers")
    args = parser.parse_args(argv)

    log("GUARDIAN | session guardian started (live MACD path; no auto-trading)")
    if not args.once:
        try:
            import cc_guardian_ctl as gctl  # noqa: E402

            gctl.write_guardian_pid(os.getpid())
        except Exception:
            pass
    try:
        if args.once:
            one_cycle(repair=not args.no_repair)
            return 0

        while session_active():
            one_cycle(repair=not args.no_repair)
            time.sleep(max(60, int(args.interval)))

        log("GUARDIAN | market window ended — guardian stopping")
        return 0
    finally:
        if not args.once:
            try:
                import cc_guardian_ctl as gctl  # noqa: E402

                gctl.clear_guardian_pid()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
