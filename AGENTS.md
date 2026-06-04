# Agent notes (Cursor)

## Plan before work

Read **`docs/CURSOR-PLAN-FIRST.md`** and **`docs/ACTIVE-PLAN.md`**. Post a short plan and wait for user **go** before editing code (unless they said implement / fix now). **Never change production** (weekly MACD template, Render env, committee) without explicit **go** + track letter (A/B/C/D/E) from ACTIVE-PLAN.

## Handoff after work

When you **fix code** in this repo, hand off to the next session before you stop:

```bat
launchers\HANDOFF-TO-CURSOR.bat "short summary of what changed"
```

Or: `python scripts/cursor_handoff.py --bullet "..." --topic "..."`

Reads/writes: `C:\Users\Shiel\Projects\spy-hybrid-v3\sync\cursor_inbox.md` (RULES + ACTIONS COMPLETE).

Full protocol: `docs/CURSOR-HANDOFF.md`.
