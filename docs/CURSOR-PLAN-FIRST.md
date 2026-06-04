# Plan before work (Cursor protocol)

**Rule:** Do not change bridge code, Render env, TV alerts, or launchers until:

1. The user has seen a short plan in chat **or** in `docs/ACTIVE-PLAN.md`, and  
2. The user **or** Cursor (with explicit user **`go` / `implement`**) authorizes work.

**Default:** plan and document only. No commits, deploys, or env edits without the word **go**.

## Session start (every Cursor chat)

1. Read `@sync/cursor_inbox.md` — **RULES + ACTIONS COMPLETE** + latest FIX handoff.
2. Read `@sync/grok_outbox.md` tail if fills/ops are in scope.
3. Read `docs/ACTIVE-PLAN.md` — current approved scope.
4. Reply with a **Plan** block (5–10 bullets max): goal, what you will touch, what you will not touch, one verification step.
5. Wait for user **go** unless the user message already says implement / execute / fix now.

## Plan block template

```markdown
## Plan (awaiting go)
- **Goal:**
- **In scope:**
- **Out of scope:** (Phase 1+, committee strict, paid APIs, etc.)
- **Files:**
- **Verify:**
- **One step for you after:**
```

## After approved work

1. Implement minimal diff (Phase 0 production safe).
2. `pytest tests/ -q` for touched areas.
3. `launchers\HANDOFF-TO-CURSOR.bat "summary"` before ending.
4. Update `docs/ACTIVE-PLAN.md` — mark items done or move to Next.

## Paste to open a planning chat

```
@AGENTS.md @docs/CURSOR-PLAN-FIRST.md @docs/ACTIVE-PLAN.md @sync/cursor_inbox.md

Plan only — do not edit code yet. Summarize ACTIVE-PLAN and propose next steps.
```
