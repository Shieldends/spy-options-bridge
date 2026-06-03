# SPY-LIVE-COMMAND — unified command center

**Project:** `SPY-LIVE-COMMAND` in `C:\Users\Shiel\spy-options-bridge`  
**One click:** `C:\Users\Shiel\Desktop\SPY-LIVE-COMMAND.bat` → `scripts/command_center_gui.py` (tkinter). Console `command_center.py` is advanced.

## What starts

| Layer | Role |
|-------|------|
| **Bridge** | Render `spy-options-bridge` — TV webhooks → Alpaca paper (Phase 0 MACD) |
| **Grok** | Reads `cursor_inbox.md`, appends `grok_outbox.md` every minute (research / diagnostics) |
| **Cursor** | Executes code, deploy, tests; updates inbox; reads `grok_outbox.md` each agent cycle |

## Background workers (same console session)

`scripts/command_center.py` spawns:

1. `dual_sync_loop.py` — 60s heartbeat, `append_sync.py`, log `Desktop\DUAL-SYNC-LOG.txt`
2. `bridge_keepalive.py` — Render `/ping` 8:00–17:00 ET
3. `redundant_test_loop.py` — pre-open matrix every 5 min until 9:30 ET or `STOP-REDUNDANT-TESTS.txt`

Supervisor thread: every 60s `GET /health` → `Desktop\COMMAND-CENTER-LOG.txt`

## Sync files (shared thread)

| File | Path |
|------|------|
| Cursor inbox | `C:\Users\Shiel\Projects\spy-hybrid-v3\sync\cursor_inbox.md` |
| Grok outbox | `C:\Users\Shiel\Projects\spy-hybrid-v3\sync\grok_outbox.md` |

**Grok:** Poll `grok_outbox.md` intent is inverse — Grok **reads** inbox, **writes** outbox. Cursor **reads** outbox when working; `dual_sync_loop` propagates via `append_sync.py`.

**User:** Leave Grok + Cursor sessions open; no secret paste in chat. Status email from `EMAIL_TO` in `.env` (default shieldinc850@gmail.com) for SMTP alerts only.

## Energy loop

```text
TradingView bar → Render bridge → Alpaca
       ↑                              ↓
  cursor_inbox ←—— Cursor deploy/tests ——→ grok_outbox
       ↑                              ↓
            Grok research (60s sync)
```

## Advanced (individual bats)

Still on Desktop: `BRIDGE-KEEPALIVE.bat`, `DUAL-SYNC-LOOP.bat`, `START-REDUNDANT-TEST-LOOP.bat`, `RUN-THURSDAY-LIVE.bat`. Use when debugging one layer only.

## Thursday production

Unchanged: 9:30 ET TV MACD alerts; optional 9:31 `BURST-PAPER-100.bat`. See `MARKET-OPEN-THU.txt` and `THURSDAY-LIVE-RUN.txt`.

## Stop

- **Ctrl+C** in the command center window — kills all three child processes
- Redundant tests only: `STOP-REDUNDANT-TESTS.bat` or 9:30 ET auto-stop
