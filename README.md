# spy-options-bridge — READY TO DEPLOY

Single file: **`main.py`** — everything in one place for Render.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Status check |
| POST | `/entry` | Entry spread + GTC 50% take-profit |
| POST | `/webhook` | Alias for `/entry` |
| POST | `/warning` | Danger alert only — no orders |

## Deploy to Render (3 steps)

1. **Push to GitHub** — upload this folder as a repo.
2. **Render** → New Web Service → connect repo → deploy (reads `render.yaml`).
   Add env vars from `.env.example` (username, password, account #, webhook secret).
3. **TradingView** — set webhook URLs to your Render URL (see JSON below).

Set `EXECUTION_MODE=production` on Render after sandbox tests pass.

See `.env.example` and `templates/` for copy-paste configs.
