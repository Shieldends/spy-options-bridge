# Render keep-alive

`GET /ping` returns `{"status":"ok","version":"..."}` — lightweight health probe.

## Paid Starter instance (recommended)

Starter (or higher) on `spy-options-bridge` stays always-on — no cold sleep. **K / Guardian optional** as backup only.

## Local Guardian (G starts this automatically)

`GO-TOMORROW-ONE-CLICK.bat` starts **Live Session Guardian**, which restarts `bridge_keepalive.py` if it dies.

Manual keepalive window (only if Guardian is off):

```text
C:\Users\Shiel\spy-options-bridge\launchers\START-BRIDGE-KEEPALIVE.bat
```

Runs `scripts/bridge_keepalive.py` — pings every **60 seconds during RTH** and **5 minutes** off-hours, weekdays.

## External cron (optional backup)

Point any free cron (e.g. cron-job.org) at:

```text
GET https://spy-options-bridge.onrender.com/ping
```

Schedule: `*/5 8-16 * * 1-5` in **America/New_York** (or equivalent UTC).

No secrets required for `/ping` or `/health`.
