# Render keep-alive (free tier)

`GET /ping` returns `{"status":"ok","version":"..."}` — lightweight; use for cron.

## Local (user leaves terminal open)

```text
C:\Users\Shiel\Desktop\BRIDGE-KEEPALIVE.bat
```

Runs `scripts/bridge_keepalive.py` — pings every **5 minutes**, **9:00–16:00 ET** Mon–Fri.

## External cron (optional)

Point any free cron (e.g. cron-job.org) at:

```text
GET https://spy-options-bridge.onrender.com/ping
```

Schedule: `*/5 9-16 * * 1-5` in **America/New_York** (or equivalent UTC).

No secrets required for `/ping` or `/health`.
