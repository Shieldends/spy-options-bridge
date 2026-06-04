#!/usr/bin/env python3
"""Launch Command Center GUI — clear stale lock, log crashes, no black-console flash."""

from __future__ import annotations

import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import command_center as cc  # noqa: E402

DESKTOP = Path(r"C:\Users\Shiel\Desktop")
CRASH = DESKTOP / "COMMAND-CENTER-CRASH.txt"
BOOT = DESKTOP / "COMMAND-CENTER-BOOT.txt"


def _log_boot(msg: str) -> None:
    line = f"{msg}\n"
    try:
        with BOOT.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def _clear_stale_gui_lock() -> None:
    if not cc.GUI_LOCK.is_file():
        return
    try:
        holder = int(cc.GUI_LOCK.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        holder = 0
    if holder and not cc._pid_alive(holder):
        cc.GUI_LOCK.unlink(missing_ok=True)  # type: ignore[arg-type]
        _log_boot(f"cleared stale GUI lock (dead PID {holder})")


def _show_error(msg: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("SPY Live Command Center", msg, parent=root)
        root.destroy()
    except Exception:
        print(msg, file=sys.stderr)


def main() -> int:
    _log_boot("cc_launcher start")
    _clear_stale_gui_lock()
    cc.stop_stale_console_supervisors()
    cc.consolidate_to_one_gui()
    if not cc.try_acquire_gui_lock():
        holder = cc.GUI_LOCK.read_text(encoding="utf-8").strip() if cc.GUI_LOCK.is_file() else "?"
        _show_error(
            "Command Center is already open on this PC.\n\n"
            f"Lock holder PID: {holder}\n\n"
            "If you see no window: run launchers\\CLEAR-GUI-LOCK.bat then try again."
        )
        return 0
    try:
        from command_center_gui import main as gui_main  # noqa: E402

        return int(gui_main())
    except Exception:
        text = traceback.format_exc()
        try:
            CRASH.write_text(text, encoding="utf-8")
        except OSError:
            pass
        _log_boot(f"crash written {CRASH}")
        _show_error(
            "Command Center failed to start.\n\n"
            f"Details saved to:\n{CRASH}\n\n"
            "Live MACD still works via Render. Run LIVE-SESSION-GUARDIAN.bat for today."
        )
        return 1
    finally:
        cc.release_gui_lock()


if __name__ == "__main__":
    raise SystemExit(main())
