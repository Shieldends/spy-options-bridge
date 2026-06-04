# Fill test + P&L audit (Tracks A + E)

## Track A — push a fill

**During RTH (9:30–16 ET):**

```bat
C:\Users\Shiel\spy-options-bridge\launchers\PUSH-PAPER-FILL-TEST.bat
```

- Calls Render `POST /exercise/entry` (sync chase, min credit on paper).
- Result: `Desktop\PAPER-FILL-TEST-RESULT.txt`
- Confirm in Alpaca **Activities**, not Orders alone.

**Render deploy:** `render.yaml` chase tuned (25 attempts, 15 floor polls, 4s wait). Manual Deploy on Render after git push.

## Track E — P&L proof

**Snapshot only:**

```bat
launchers\PAPER-PNL-AUDIT.bat
```

**Snapshot + one entry attempt (RTH):**

```bat
launchers\PAPER-PNL-AUDIT.bat --try-entry
```

Report: `Desktop\PAPER-PNL-AUDIT.txt` — equity before/after, delta, recent mleg orders.

## Track D

Not started until at least one fill path looks good (`go D` later).
