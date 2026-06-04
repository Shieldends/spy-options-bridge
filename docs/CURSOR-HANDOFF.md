# Cursor handoff (when fixes land)

After any **spy-options-bridge** code fix, hand context to the next Cursor session:

```bat
launchers\HANDOFF-TO-CURSOR.bat "one-line summary of the fix"
```

Or from Python:

```python
from cursor_handoff import handoff, queue_fix

handoff("topic", ["bullet 1", "bullet 2"], next_step="what Cursor should do next")
```

## What it updates

| Target | Purpose |
|--------|---------|
| `Projects\spy-hybrid-v3\sync\cursor_inbox.md` | **RULES + ACTIONS COMPLETE** — Cursor reads each session |
| `sync\grok_outbox.md` | Grok mirror |
| `sync\inbox.jsonl` → `append_sync.py` | `DECISIONS.md` |
| `Desktop\CURSOR-LAST-HANDOFF.txt` | Paste snippet for Cursor chat |

## Automatic hooks

- **ARM** (`arm_for_open.py`) — handoff after each ARM
- **dual_sync_loop** — drains `sync\fixes_pending.jsonl` every 60s
- **Queue without immediate flush:** `queue_fix(..., flush_now=False)` then dual_sync drains

## Agent rule

When you change bridge code in Cursor, run handoff (or queue) **before ending the task** so the next chat does not re-discover the same bug.
