# Render memory limit (spy-options-bridge)

## What happened

Render **free tier** (~512 MB RAM) restarted the service when memory spiked. That causes **brief 502/503** on webhooks (seen in `BURST-PAPER-LOG.txt`).

## Live MACD impact

| Path | Impact |
|------|--------|
| **TV → `/entry`** | Short outage during restart; alerts may show Failed once. Retries OK after `/health` returns. |
| **Paper burst 100** | **Primary suspect** — one HTTP request runs many sync chases and holds a large `attempts[]` payload. |
| **Keepalive `/ping`** | Low memory; keep running on PC. |
| **Local Command Center** | Unaffected (runs on your PC). |

Phase 0 production MACD is **not** burst — but **do not** run `BURST-PAPER-100.bat` during the live MACD window.

## On our side (fixed in repo — deploy required)

1. **`BURST_MAX_COUNT=10`** on Render — caps `/exercise/burst` and burst JSON on `/entry`.
2. **One burst at a time** per instance (`_burst_in_progress`).
3. **Max 2 concurrent background chase** tasks (`MAX_CONCURRENT_CHASE_TASKS=2`).
4. **API returns last 5 attempts only** — not full 100-row JSON.

Long bursts: use `scripts/burst_paper_fills.py` from your PC (many small requests), not `count=100` in one POST.

## Your checklist

1. **Manual Deploy** on Render after git push (memory patch + go A+E chase env).
2. **Do not** run `Desktop\BURST-PAPER-100.bat` while MACD alerts are live.
3. Ensure **`REDUNDANT_BURST_EACH_CYCLE`** is **not** set in your PC env (would hit Render every 5 min).
4. If restarts continue: upgrade Render plan **or** keep burst tests to **≤5** per request off-hours.
5. After deploy: `GET /health` — confirm service stable 30+ minutes.

## If you need more memory without code

Render dashboard → spy-options-bridge → **upgrade instance** (Starter has more RAM).
