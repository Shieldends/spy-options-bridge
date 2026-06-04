# Command Center 2.0 — architecture (plan, not scrap)

**Status:** Plan for post-close build. **Today:** live MACD + `live_session_guardian.py` (no GUI required).

## Vision

One **professional operations desk** — not a phone app, not bloated IDE software:

| Pillar | Role |
|--------|------|
| **You** | Approve, goals, TradingView, Alpaca truth |
| **Super Grok Heavy** | Research, diagnostics, `grok_outbox.md` |
| **Cursor** | Engineer + deploy, `cursor_inbox.md`, bridge code |

Same room: **status · comms · execute** without fighting duplicate processes.

## What we keep (v1 assets)

- Render bridge Phase 0 MACD
- `dual_sync_loop`, `bridge_keepalive`, sync files
- Operator grant + email YES/NO protocol
- Desktop launchers (thin wrappers)
- `paper_pnl_audit`, `trigger_chain_proof`, burst tools (off-hours)

## v1 problems (today)

- GUI + console supervisor fought → worker respawn / black console
- tkinter fragile on Windows (lock, python vs pythonw)
- Too many buttons on one tab; unclear “READY” vs “filled”
- Success = **MACD fill in Activities**, not “helpers running”

## 2.0 design pillars

### 1. Single supervisor

- **Only** GUI *or* headless guardian — never both restarting workers
- `cc_launcher.py` + `CLEAR-GUI-LOCK.bat` for clean open
- `live_session_guardian.py` when GUI down (today’s salvage)

### 2. Three-panel UI (tkinter or lightweight web local)

| Panel | Content |
|-------|---------|
| **Live** | Bridge health, tv_risk, open orders, READY banner |
| **Team** | Grok/Cursor sync open buttons, last handoff line |
| **Actions** | START TEAM, STOP ALL, reports — fewer, larger buttons |

Dark-neutral palette, Segoe UI, no consumer gradients.

### 3. Session lifecycle

| Phase | Tool |
|-------|------|
| Pre-open | redundant tests (optional) |
| RTH | guardian + Render keepalive |
| EOD | `eod_session_report.py` → Desktop + email |

### 4. Approval path

Email subject: `[SPY Command Center] NEED APPROVAL - Command Center 2.0 sprint`  
Reply **YES** / **go CC2** to authorize implementation sprints.

## Implementation tracks (after **go CC2**)

1. **2.0a** — Launcher + lock + guardian default (today partial)
2. **2.0b** — UI shell refactor (3 panels, status bar)
3. **2.0c** — Embedded log tail + one-click EOD
4. **2.0d** — Optional local dashboard (FastAPI localhost) if tkinter still fails

## Out of scope for 2.0

- Replacing Render bridge
- Phase 1 committee Pine without explicit gate
- Gmail read automation without permission
