#!/usr/bin/env python3
"""Sync non-secret spread env vars from render.yaml to live Render (optional API path).

Requires RENDER_API_KEY in environment or .env. If missing, prints the vars to set manually.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
RENDER_YAML = ROOT / "render.yaml"
API = "https://api.render.com/v1"


def _load_api_key() -> str | None:
    key = os.environ.get("RENDER_API_KEY", "").strip()
    if key:
        return key
    env_path = ROOT / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("RENDER_API_KEY="):
                return s.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _yaml_env_updates() -> dict[str, str]:
    data = yaml.safe_load(RENDER_YAML.read_text(encoding="utf-8"))
    services = data.get("services") or []
    svc = next((s for s in services if s.get("name") == "spy-options-bridge"), None)
    if not svc:
        raise RuntimeError("spy-options-bridge service not found in render.yaml")
    out: dict[str, str] = {}
    for item in svc.get("envVars") or []:
        if item.get("sync") is False:
            continue
        key = item.get("key")
        if not key or "value" not in item:
            continue
        out[str(key)] = str(item["value"])
    return out


def _find_service_id(headers: dict[str, str]) -> str:
    cursor = ""
    while True:
        params: dict[str, str | int] = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        r = httpx.get(f"{API}/services", headers=headers, params=params, timeout=30)
        r.raise_for_status()
        payload = r.json()
        for row in payload:
            svc = row.get("service") or row
            name = svc.get("name") or svc.get("slug")
            if name == "spy-options-bridge":
                return str(svc["id"])
        cursor = ""
        if isinstance(payload, list) and payload:
            last = payload[-1]
            if isinstance(last, dict) and last.get("cursor"):
                cursor = last["cursor"]
        if not cursor:
            break
    raise RuntimeError("spy-options-bridge service not found in Render account")


def _get_env_vars(service_id: str, headers: dict[str, str]) -> list[dict[str, str]]:
    r = httpx.get(f"{API}/services/{service_id}/env-vars", headers=headers, timeout=30)
    r.raise_for_status()
    rows = r.json()
    out: list[dict[str, str]] = []
    for row in rows:
        ev = row.get("envVar") or row
        out.append({"key": str(ev["key"]), "value": str(ev.get("value", ""))})
    return out


def _put_env_vars(service_id: str, headers: dict[str, str], env: list[dict[str, str]]) -> None:
    r = httpx.put(
        f"{API}/services/{service_id}/env-vars",
        headers=headers,
        json=[{"key": e["key"], "value": e["value"]} for e in env],
        timeout=60,
    )
    r.raise_for_status()


def _trigger_deploy(service_id: str, headers: dict[str, str]) -> None:
    r = httpx.post(
        f"{API}/services/{service_id}/deploys",
        headers=headers,
        json={"clearCache": "do_not_clear"},
        timeout=30,
    )
    r.raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync render.yaml spread env to Render")
    parser.add_argument("--dry-run", action="store_true", help="Print merged env only")
    parser.add_argument("--no-deploy", action="store_true", help="Update env without deploy")
    args = parser.parse_args()

    updates = _yaml_env_updates()
    print(f"render.yaml spread keys: {len(updates)}")

    api_key = _load_api_key()
    if not api_key:
        print("RENDER_API_KEY missing — set in .env or environment for API sync.")
        print("Falling back: code defaults in main.py + git deploy also apply when dashboard vars unset.")
        for k, v in sorted(updates.items()):
            print(f"  {k}={v}")
        return 1

    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    service_id = _find_service_id(headers)
    current = _get_env_vars(service_id, headers)
    merged = {e["key"]: e["value"] for e in current}
    merged.update(updates)
    payload = [{"key": k, "value": v} for k, v in sorted(merged.items())]

    if args.dry_run:
        for item in payload:
            if item["key"] in updates:
                print(f"SET {item['key']}={item['value']}")
        return 0

    _put_env_vars(service_id, headers, payload)
    print(f"Updated env on service {service_id}")
    if not args.no_deploy:
        _trigger_deploy(service_id, headers)
        print("Deploy triggered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
