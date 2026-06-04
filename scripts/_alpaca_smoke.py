from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
env: dict[str, str] = {}
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
h = {
    "Apca-Api-Key-Id": env.get("APCA_API_KEY_ID", ""),
    "Apca-Api-Secret-Key": env.get("APCA_API_SECRET_KEY", ""),
}
base = (env.get("APCA_API_BASE_URL") or "https://paper-api.alpaca.markets").rstrip("/")
r = httpx.get(f"{base}/v2/account", headers=h, timeout=30)
print("ACCOUNT_HTTP", r.status_code)
if r.is_success:
    j = r.json()
    print("ACCOUNT_STATUS", j.get("status"), "EQUITY", j.get("equity"))
