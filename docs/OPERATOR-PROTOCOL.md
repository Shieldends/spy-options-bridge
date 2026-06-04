# Operator Protocol v1



User-granted, time-limited control so Cursor/agents can run **whitelisted** Windows actions without bypassing your consent.



## Default: deny



Nothing runs until `C:\Users\Shiel\Desktop\OPERATOR-GRANT.json` exists and is unexpired. **Revoke:** delete that file.



## What “real movement” means



| Version | What happens | Grant |

|---------|----------------|-------|

| **v1 (now)** | Starts programs, opens HTTPS URLs in the default browser, launches SPY `.bat` files, opens folders, runs whitelisted shell commands | `session` via `GRANT-OPERATOR-FULL.bat` or `GRANT-OPERATOR-SESSION.bat` |

| **v2 (later)** | Mouse/keyboard UI automation (click TradingView buttons, etc.) | Tier `automate` in YAML is **OFF** (empty actions). Optional `pywinauto` / `pyautogui` — not shipped in v1 |



v1 does **not** move the mouse or send keystrokes. It **opens** Cursor, Alpaca, TradingView, Command Center bats, and Explorer paths — the same as you double-clicking, but only through the gateway after you grant.



## Tiers (`config/operator_protocol.yaml`)



| Tier | Minutes | Actions |

|------|---------|---------|

| `observe` | 30 | `health` only |

| `launch` | 60 | `launch`, `open` |

| `shell` | 60 | `shell` |

| `session` | 60 | `launch`, `open`, `shell`, `program`, `url` |

| `automate` | 30 | *(none — reserved v2)* |



**Windows services:** `service_start` / `service_stop` exist in the gateway but `service_whitelist` is **empty** → always denied until you add SPY-related service names and allow them in a tier.



Grants are written **only** by you via Desktop bat (Y/N), not by the agent from chat.



## How to grant (user)

**Minimum path:** double-click `C:\Users\Shiel\Desktop\ARM-FOR-OPEN-ONE-CLICK.bat` once before market (see `launchers/OPERATOR-QUICK-START.txt`). That writes a 12h session grant, starts the command center supervisor, schedules 9:31 ET burst, and logs to `ARM-FOR-OPEN.log`.

1. **Full session (recommended):** double-click `C:\Users\Shiel\Desktop\GRANT-OPERATOR-FULL.bat` → **Y** → 60 min `session` (launch + shell + **program** + **url**).

2. **Basic session:** `GRANT-OPERATOR-SESSION.bat` → same tier, shorter prompt.

3. **Run actions:** `RUN-OPERATOR-ACTION.bat` menu, or pass-through:

   - `RUN-OPERATOR-ACTION.bat health`

   - `RUN-OPERATOR-ACTION.bat program cursor_project`

   - `RUN-OPERATOR-ACTION.bat url https://app.alpaca.markets/`

   - `RUN-OPERATOR-ACTION.bat launch "C:\Users\Shiel\Desktop\SPY-LIVE-COMMAND-CENTER.bat"`

4. **Audit:** `C:\Users\Shiel\Desktop\OPERATOR-AUDIT.log`

5. **Revoke:** delete `OPERATOR-GRANT.json`.



## Agent / Cursor usage



After a valid grant exists (you ran the GRANT bat first):



```text

C:\Users\Shiel\spy-options-bridge\.venv\Scripts\python.exe scripts\operator_gateway.py --action health --target https://spy-options-bridge.onrender.com/health

```



```text

... operator_gateway.py --action program --target cursor_project

```



```text

... operator_gateway.py --action program --target cursor

```



```text

... operator_gateway.py --action url --target https://www.tradingview.com/

```



```text

... operator_gateway.py --action launch --target C:\Users\Shiel\Desktop\RUN-LAB.bat

```



```text

... operator_gateway.py --action shell --target "pytest tests/test_operator_gateway.py -q"

```



Agents **must not** call `--grant-tier` themselves; you run a GRANT bat.



## Whitelists



| Action | Config key | Notes |

|--------|------------|-------|

| `program` | `program_whitelist` | Aliases: `cursor`, `cursor_project`, `notepad`; or full `.exe` on `paths` list under `C:\Users\Shiel` |

| `url` | `url_whitelist` | Prefix match — Alpaca app, TradingView, Render health |

| `launch` | `launch_whitelist` | SPY Desktop bats |

| `service_*` | `service_whitelist` | Default `[]` = deny all |



Alpaca and TradingView use **`url`**, not a local Alpaca `.exe`.



## Paths



| Artifact | Path |

|----------|------|

| Config | `C:\Users\Shiel\spy-options-bridge\config\operator_protocol.yaml` |

| Gateway | `C:\Users\Shiel\spy-options-bridge\scripts\operator_gateway.py` |

| Grant | `C:\Users\Shiel\Desktop\OPERATOR-GRANT.json` |

| Audit | `C:\Users\Shiel\Desktop\OPERATOR-AUDIT.log` |

| Grant FULL (Desktop) | `C:\Users\Shiel\Desktop\GRANT-OPERATOR-FULL.bat` |

| Grant session (repo) | `launchers\operator\GRANT-OPERATOR-SESSION.bat` |

| Action bat (repo) | `launchers\operator\RUN-OPERATOR-ACTION.bat` |



## Command Center GUI



Optional button **Operator: grant session** runs the same grant subprocess (same as the bat).


