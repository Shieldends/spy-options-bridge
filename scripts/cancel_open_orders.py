#!/usr/bin/env python3
"""Cancel all open Alpaca orders (paper-safe)."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def headers(env: dict[str, str]) -> dict[str, str]:
    return {
        "Apca-Api-Key-Id": env["APCA_API_KEY_ID"],
        "Apca-Api-Secret-Key": env["APCA_API_SECRET_KEY"],
    }


def base_url(env: dict[str, str]) -> str:
    return env.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")


def list_open(client: httpx.Client, base: str, h: dict[str, str]) -> list[dict]:
    r = client.get(f"{base}/v2/orders", headers=h, params={"status": "open", "limit": 500, "nested": True})
    if not r.is_success:
        print(f"FAIL list open orders HTTP {r.status_code}")
        sys.exit(1)
    body = r.json()
    return body if isinstance(body, list) else []


def main() -> int:
    parser = argparse.ArgumentParser(description="Cancel open Alpaca paper orders")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List open orders only; do not cancel",
    )
    args = parser.parse_args()

    env = load_env()
    base = base_url(env)
    if "paper-api.alpaca.markets" not in base:
        print(f"FAIL refusing non-paper base: {base}")
        return 1
    h = headers(env)
    canceled = 0
    failed = 0
    with httpx.Client(timeout=30.0) as client:
        open_before = list_open(client, base, h)
        print(f"Open orders before: {len(open_before)}")
        if args.dry_run:
            for o in open_before[:10]:
                oc = o.get("order_class") or "simple"
                print(
                    f"  would cancel {str(o.get('id', ''))[:8]} "
                    f"class={oc} status={o.get('status')} limit={o.get('limit_price')}"
                )
            if len(open_before) > 10:
                print(f"  ... and {len(open_before) - 10} more")
            print("RESULT: PASS (dry-run)")
            return 0
        if open_before:
            bulk = client.delete(f"{base}/v2/orders", headers=h)
            if bulk.is_success:
                canceled = len(open_before)
                print(f"Bulk cancel requested for {canceled} open order(s) HTTP {bulk.status_code}")
            else:
                for o in open_before:
                    oid = o.get("id")
                    if not oid:
                        continue
                    oc = o.get("order_class") or "simple"
                    cr = client.delete(f"{base}/v2/orders/{oid}", headers=h)
                    if cr.is_success:
                        canceled += 1
                        print(
                            f"Canceled {oid[:8]} class={oc} status={o.get('status')} "
                            f"limit={o.get('limit_price')}"
                        )
                    else:
                        failed += 1
                        print(f"FAIL cancel {oid[:8]} HTTP {cr.status_code}")
        open_after = open_before
        for attempt in range(8):
            open_after = list_open(client, base, h)
            if not open_after:
                break
            time.sleep(0.75 * (attempt + 1))
        print(f"Canceled: {canceled}, failed: {failed}, open remaining: {len(open_after)}")
    if failed or open_after:
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
