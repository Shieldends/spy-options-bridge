# Command Center 2.0 — team development & deployment hub

**Status:** ACTIVE — revised for **You · Grok · Cursor** workflow (not a trading babysitter UI).

## Primary purpose

**One room to build and ship the trading system together:**

| Who | Channel |
|-----|---------|
| **You** | Goals, YES/NO email, TradingView, Alpaca truth |
| **Super Grok Heavy** | `grok_outbox.md` — research, diagnostics |
| **Cursor** | `cursor_inbox.md` — engineer, push, Render deploy |

The app is your **desk for development and deployment** — not the trader execution engine (that stays on **Render + TradingView**).

## GUI tabs (2.0 revised)

| Tab | Purpose |
|-----|---------|
| **Team** | Open inbox/outbox, post handoff to Cursor, active plan, repo folder |
| **Deploy** | Deploy approval email, operator grant, WHAT-YOU-DO-NOW, email reports |
| **Live review** | *Optional* — bridge health, Guardian on/off, journal tail, EOD |

**Default tab:** Team (opens here).

## Secondary: babysitter (optional)

- **Live Session Guardian** — headless; keeps sync + Render warm; logs journal
- Use **Live review** tab or `START-GUARDIAN.bat` only when you want “what’s up”
- **Not required** for MACD alerts

## Console flashes (blue/black)

- Blue = PowerShell; black = `.bat` / old console supervisor
- Fixed: hidden PowerShell for worker checks (no random blue flash)
- Avoid: ARM + GUI fight, MONITOR bat in a loop, `BURST-100` during MACD
- Once: `launchers\STOP-BLUE-FLASH.bat` if flashes return

## What we keep

- Phase 0 bridge, sync files, email protocol, launchers, pytest
- Guardian + EOD report as **review tools**, not the product center

## Out of scope

- Replacing Render or Phase 1 committee without explicit gate
- Gmail read automation without permission
