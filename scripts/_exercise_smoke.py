"""Smoke POST /exercise/entry and /exercise/burst without printing secrets."""
from pathlib import Path
import json
import httpx

ROOT = Path(__file__).resolve().parents[1]
RENDER = "https://spy-options-bridge.onrender.com"
env: dict[str, str] = {}
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
secret = env.get("WEBHOOK_SECRET", "")
body = {
    "webhookSecret": secret,
    "ticker": "SPY",
    "action": "PUT_CREDIT_SPREAD",
    "signalPrice": 590.0,
    "dteFilter": "weekly",
    "strikeOffsetShort": -5,
    "strikeOffsetLong": -6,
    "quantity": 1,
    "fillMode": "exercise",
    "skipExits": True,
}

for label, path, extra in (
    ("EXERCISE_ENTRY", "/exercise/entry", {}),
    ("EXERCISE_BURST3", "/exercise/burst?count=3&interval=1", {"burstCount": 3}),
):
    r = httpx.post(f"{RENDER}{path}", json={**body, **extra}, timeout=300)
    print(label, "HTTP", r.status_code)
    try:
        d = r.json()
        keys = ("success", "message", "filled", "filled_count", "status", "order_id")
        print(json.dumps({k: d.get(k) for k in keys if k in d}, indent=2)[:500])
    except Exception:
        print(r.text[:200])
