# Active plan (consult before work)

**Status:** **go A+E + Render 5.5.11 live** — local team needs one GUI START TEAM after dedupe  
**Updated:** 2026-06-04 ET (~12:47)  
**Gate:** Track **D** still blocked until you say **`go D`**.

### go A+E — done in repo

| Track | Delivered |
|-------|-----------|
| **A** | `render.yaml` chase tuning; `PUSH-PAPER-FILL-TEST.bat`; `force_paper_fill_now.py` → `/exercise/entry` + `Desktop\PAPER-FILL-TEST-RESULT.txt` |
| **E** | `paper_pnl_audit.py` + `PAPER-PNL-AUDIT.bat` → `Desktop\PAPER-PNL-AUDIT.txt` |
| **Docs** | `docs/FILL-AND-PNL-AUDIT.md` |

### Your steps after push

1. Render dashboard → **Manual Deploy** (chase env from `render.yaml`).
2. RTH: `launchers\PUSH-PAPER-FILL-TEST.bat` then `launchers\PAPER-PNL-AUDIT.bat --try-entry`.
3. Alpaca → **Activities** + read Desktop audit files.
4. When fills look good → reply **`go D`** for 0dte hold playbook.

---

## User goals (combined)

1. **More fills** — paper entries that actually fill (not only submitted/canceled).
2. **Hold some positions through the session** — options that **expire same day**; manage through **market close** (not closed early by mistake).
3. **Prove Alpaca “money side” works** — see **gain/loss** in account **balance/equity** and **Activities** so the stack feels real before scaling.
4. **Plan-first** — Cursor reads this + `docs/CURSOR-PLAN-FIRST.md`; **consult, then wait for go**.

---

## Current facts (do not re-discover)

| Item | State |
|------|--------|
| Phase | **0 only** — MACD Entry/Warning → Render → Alpaca paper |
| Entry DTE (production) | `dteFilter: weekly` in `templates/tradingview-entry-autofill.json` → expires **Friday**, not EOD today |
| EOD same-day options | Bridge supports `dteFilter: "0dte"` / `today` in `resolve_dte_expiration()` — **not** production TV template yet |
| Warning / close | `/warning` + `AUTO_CLOSE_ON_WARNING`; `overrideAutoClose: true` **skips** close; `forceAutoClose: true` **forces** close (`exercise_system.py` pattern) |
| Fill plumbing | `PAPER_FORCE_MIN_FILL`, chase, `/exercise/entry`, burst, `force_paper_fill_now.py` |
| Account read | `scripts/_alpaca_smoke.py`, preopen matrix Alpaca account line — **no** standing Desktop P&L audit bat yet |
| Ops | GUI **or** ARM; `MONITOR-AND-READY.bat`; no full dedupe during session |

---

## Proposed tracks (implementation **only after go**)

### Track A — Fill rate (unchanged)

- Render chase env check (`ENTRY_MIN_CREDIT`, chase attempts/floor polls).
- Optional `PUSH-PAPER-FILL-TEST.bat` → `force_paper_fill_now.py` + Desktop result line.
- Doc: verify in Alpaca **Activities**, not Orders tab.

**Verify:** ≥1 RTH fill via `/exercise/entry` or documented fail (403 / 502 / chase exhausted).

---

### Track B — Position count vs burst (unchanged)

- Docs: more open spreads = more MACD **signals** or higher TV **`quantity`**; burst = **proof**, not portfolio builder.

**Verify:** You confirm desired `quantity` and alert frequency.

---

### Track C — Ops stability (unchanged)

- `MONITOR-AND-READY.bat`; supervisor-only dedupe; single control path (GUI **or** console).

---

### Track D — Hold through close + same-day expiry (NEW)

**Intent:** Keep selected spreads **open intraday**; contracts **expire EOD** (0DTE) or you **close at close** deliberately.

| Step | Plan (no change until go) |
|------|---------------------------|
| D1 | Add **separate** TV template `tradingview-entry-0dte-hold.json` — `dteFilter: "0dte"`, same offsets; do **not** replace production weekly template without approval |
| D2 | Warning discipline: while holding — TV Warning with **`overrideAutoClose: true`** so MACD warning does **not** flatten early; at **~15:55 ET** — one-shot Warning with **`forceAutoClose: true`** (or exercise_system close) to simulate exit before expiry |
| D3 | Doc in `docs/EOD-HOLD-PLAYBOOK.md`: weekly vs 0dte, hold vs auto-close, assignment/expiry risks on paper |
| D4 | Optional: Windows scheduled task or Command Center checklist item “EOD close fired” (manual confirm first week) |

**Verify:** One 0DTE spread **filled**, still open at 15:00 ET, closed or expired with matching Activities; no accidental warning close mid-day.

**Out of scope:** Auto 0dte in production MACD without explicit **`go D`**.

---

### Track E — Balance / P&L proof (NEW)

**Intent:** Show **equity/cash** moved after a round-trip (open → close or expiry) so you trust Alpaca + bridge.

| Step | Plan (no change until go) |
|------|---------------------------|
| E1 | New script `scripts/paper_pnl_audit.py` (read-only + one optional exercise entry): snapshot `GET /v2/account` (equity, cash, buying_power), run **one** controlled fill+close path, snapshot again, pull recent **Activities** / filled orders, write `Desktop\PAPER-PNL-AUDIT.txt` |
| E2 | Desktop `PAPER-PNL-AUDIT.bat` — double-click audit; **no** secrets in file |
| E3 | Extend burst summary line: `equity_before` / `equity_after` when `--audit` flag set (optional) |
| E4 | Checklist row in Command Center / WHAT-YOU-DO-NOW: “P&L audit PASS = equity delta or closed P&L line in Activities” |

**Verify:** Audit file shows two equity snapshots + ≥1 fill Activity + explainable delta (or flat if paper sim skips P&L).

**Out of scope:** Tax reporting, live broker, changing risk sizing.

---

## Recommended order

1. **C** — team READY  
2. **E** — P&L audit (proves account side once fills exist)  
3. **A** — chase/fill tuning  
4. **D** — 0dte hold playbook + templates (after at least one fill proven)  
5. **B** — quantity / alert expectations  

---

## What already works (use before building new)

- `python scripts/_alpaca_smoke.py` — quick equity read  
- `python scripts/force_paper_fill_now.py` — one entry + poll  
- `python scripts/burst_paper_fills.py --count 5 --wait-for-open` — fill chain proof  
- `scripts/exercise_system.py` + `UNDO-EXERCISE.bat` — entry + warning close + cleanup  
- Preopen matrix — Alpaca account line in report  

---

## Cursor consult (paste — plan only)

```
@docs/CURSOR-PLAN-FIRST.md @docs/ACTIVE-PLAN.md @sync/cursor_inbox.md @.cursorrules

Plan only. Do not edit code, Render, or TV alerts.
Summarize Tracks D (EOD hold) and E (P&L audit). Wait for my go.
```

---

## User approval (pick one)

| Command | Meaning |
|---------|---------|
| **`go E`** | P&L audit script + bat only |
| **`go A`** | Fill/chase tuning only |
| **`go D`** | 0dte hold playbook + templates only |
| **`go A+E`** | Fills + P&L proof (recommended first) |
| **`go A+D+E`** | Full table except B unless you add **`+B`** |
| **`go all`** | Everything in proposed tracks |

Edit this file first if you want different hold/close times or weekly instead of 0dte.
