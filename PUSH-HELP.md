# Push Help — get scale-winner settings onto Render

Your PC does **not** have Git installed yet. Pick **Path A** (best) or **Path B** (fastest, no install).

---

## Path B — Fastest (5 minutes, no Git) — do this for tomorrow

Update Render **Environment** to match scale-lab winner (same as `render.yaml` on disk):

| Variable | Value |
|----------|-------|
| `DEFAULT_STRIKE_OFFSET_SHORT` | `-5` |
| `DEFAULT_STRIKE_OFFSET_LONG` | `-6` |
| `DEFAULT_LIMIT_CREDIT` | `0.55` |
| `TAKE_PROFIT_PCT` | `0.40` |
| `STOP_LOSS_MULTIPLIER` | `1.5` |

Steps:

1. Open https://dashboard.render.com
2. Click service **spy-options-bridge**
3. **Environment** → edit each variable above → **Save**
4. **Manual Deploy** → **Deploy latest commit** (or **Clear build cache & deploy**)
5. Wait ~2–5 min, then open: https://spy-options-bridge.onrender.com/health

TradingView: copy JSON from `templates\tradingview-scaled-winner.json` into your alert message (one-time in TradingView UI).

---

## Path A — Git + GitHub (one-time setup, auto deploys later)

### 1. Install Git

```powershell
winget install Git.Git
```

Close and reopen PowerShell (or Cursor terminal).

### 2. Create GitHub repo (browser)

1. https://github.com/new
2. Name: `spy-options-bridge`
3. **Private** recommended
4. Do **not** add README (you already have files)
5. Create repository — copy the HTTPS URL, e.g. `https://github.com/YOURUSER/spy-options-bridge.git`

### 3. Push from your PC

```powershell
cd C:\Users\Shiel\spy-options-bridge

git init
git add main.py render.yaml requirements.txt templates .gitignore README.md app src tests scripts
git commit -m "Scale winner params: -5/-6 credit 0.55 TP 40% SL 1.5x"

git branch -M main
git remote add origin https://github.com/YOURUSER/spy-options-bridge.git
git push -u origin main
```

GitHub will ask you to sign in (browser or token). **Do not commit `.env`** — it is in `.gitignore`.

### 4. Connect Render to GitHub

1. Render dashboard → **spy-options-bridge**
2. **Settings** → **Build & Deploy** → confirm repo connected
3. If not connected: **Settings** → link GitHub repo → select `spy-options-bridge`
4. **Manual Deploy** after push

Every future `git push` can auto-deploy Render.

---

## Verify after either path

```powershell
Invoke-WebRequest -Uri "https://spy-options-bridge.onrender.com/health" -UseBasicParsing
```

Expect: `configured":"True"`, `broker":"alpaca"`.

---

## What you never put on GitHub

- `.env` (secrets)
- Alpaca keys
- `WEBHOOK_SECRET`

Those stay only in Render **Environment** and local `.env`.