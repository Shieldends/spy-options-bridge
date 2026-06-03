#!/usr/bin/env python3
"""SPY Live Command — tkinter meeting place (stdlib). UX layer on command_center workers."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import command_center as cc  # noqa: E402
import setup_email_env as email_setup  # noqa: E402
import todo_checklist as tc  # noqa: E402

DESKTOP = Path(r"C:\Users\Shiel\Desktop")
SYNC_DIR = Path(r"C:\Users\Shiel\Projects\spy-hybrid-v3\sync")
STOP_FILE = Path(r"C:\Users\Shiel\Desktop\STOP-REDUNDANT-TESTS.txt")
BURST_BAT = DESKTOP / "BURST-PAPER-100.bat"
REPORT_PATHS = (
    DESKTOP / "LIVE-RUN-READINESS-REPORT.txt",
    DESKTOP / "FINAL-TEAM-AUDIT.txt",
)
GROK_PATHS = (
    SYNC_DIR / "cursor_inbox.md",
    SYNC_DIR / "grok_outbox.md",
)
ET = ZoneInfo("America/New_York")
BTN_FONT = ("Segoe UI", 11, "bold")
TITLE = "SPY Live Command"


class CommandCenterApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(TITLE)
        self.geometry("560x720")
        self.minsize(480, 600)
        cc._load_dotenv()

        self.procs: dict[str, subprocess.Popen[bytes]] = {}
        self._health_stop = threading.Event()
        self._health_thread: threading.Thread | None = None
        self._check_vars: dict[str, tk.BooleanVar] = {}
        self._status_var = tk.StringVar(value="Ready — click RENDER STATUS or START TEAM")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(500, self._refresh_checklist_ui)

    def _build_ui(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        main = ttk.Frame(notebook, padding=8)
        checklist = ttk.Frame(notebook, padding=8)
        help_tab = ttk.Frame(notebook, padding=12)
        notebook.add(main, text="Commands")
        notebook.add(checklist, text="Checklist")
        notebook.add(help_tab, text="Help")

        buttons: list[tuple[str, callable]] = [
            ("START TEAM", self._start_team),
            ("WHAT'S LEFT?", self._whats_left),
            ("SETUP EMAIL", self._setup_email_dialog),
            ("TEST EMAIL", self._test_email),
            ("OPEN REPORTS", self._open_reports),
            ("OPEN GROK SYNC", self._open_grok_sync),
            ("THURSDAY BURST", self._thursday_burst),
            ("STOP ALL", self._stop_all),
            ("RENDER STATUS", self._render_status),
        ]
        for idx, (label, cmd) in enumerate(buttons):
            row, col = divmod(idx, 2)
            ttk.Button(main, text=label, command=cmd, width=22).grid(
                row=row, column=col, padx=6, pady=6, sticky="ew"
            )
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=1)

        canvas = tk.Canvas(checklist, highlightthickness=0)
        scroll = ttk.Scrollbar(checklist, orient=tk.VERTICAL, command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        for key in tc.DEFAULT_ITEMS:
            var = tk.BooleanVar(value=False)
            self._check_vars[key] = var
            text = tc.LABELS.get(key, key)
            cb = ttk.Checkbutton(
                inner,
                text=text,
                variable=var,
                command=lambda k=key: self._on_check_toggle(k),
                wraplength=480,
            )
            cb.pack(anchor=tk.W, pady=4, padx=4)

        ttk.Button(
            checklist, text="Refresh checklist", command=self._refresh_checklist_ui
        ).pack(pady=6)

        help_text = (
            "SPY Live Command — one window for pre-market ops.\n\n"
            "• START TEAM — dual sync, bridge keepalive, redundant tests (same as console center).\n"
            "• TradingView → Render → Alpaca Thursday path is unchanged.\n"
            "• Desktop .bat files remain for advanced / single-layer debugging.\n\n"
            "Phase 2 master plan — locked until live test proven."
        )
        ttk.Label(help_tab, text=help_text, justify=tk.LEFT, wraplength=500).pack(
            anchor=tk.W
        )

        bar = ttk.Label(
            self,
            textvariable=self._status_var,
            relief=tk.SUNKEN,
            anchor=tk.W,
            padding=(6, 4),
        )
        bar.pack(side=tk.BOTTOM, fill=tk.X)

    def _set_status(self, msg: str) -> None:
        ts = datetime.now(ET).strftime("%H:%M:%S ET")
        self._status_var.set(f"{ts} | {msg}")

    def _refresh_checklist_ui(self) -> None:
        data = tc.load_checklist()
        for key, var in self._check_vars.items():
            var.set(bool(data["items"].get(key, False)))

    def _on_check_toggle(self, key: str) -> None:
        done = self._check_vars[key].get()
        tc.set_item(key, done)
        tc.write_human_summary()
        self._set_status(f"Checklist: {tc.LABELS.get(key, key)} → {'done' if done else 'open'}")

    def _start_team(self) -> None:
        if self.procs and any(p.poll() is None for p in self.procs.values()):
            messagebox.showinfo(TITLE, "Team workers already running.")
            return
        cc.mark_todo_items()
        self.procs = cc.spawn_all_workers()
        self._set_status("START TEAM — 3 workers spawned")
        self._start_health_loop()
        messagebox.showinfo(
            TITLE,
            "Team started:\n• dual_sync_loop (60s)\n• bridge_keepalive\n• redundant_test_loop (5 min)\n\n"
            "Use STOP ALL before closing the window.",
        )

    def _start_health_loop(self) -> None:
        self._health_stop.clear()
        if self._health_thread and self._health_thread.is_alive():
            return

        def loop() -> None:
            while not self._health_stop.wait(cc.INTERVAL_SEC):
                ok, detail = cc.fetch_health()
                msg = f"health {'OK' if ok else 'WARN'} | {detail}"
                self.after(0, lambda m=msg: self._set_status(m))

        self._health_thread = threading.Thread(target=loop, daemon=True)
        self._health_thread.start()

    def _whats_left(self) -> None:
        txt_path = tc.CHECKLIST_PATH.with_suffix(".txt")
        tc.write_human_summary(txt_path)
        cc.open_notepad(tc.CHECKLIST_PATH.with_suffix(".txt"))
        lines = tc.format_incomplete_lines()
        if lines:
            body = "Still open:\n\n" + "\n".join(lines)
        else:
            body = "All checklist items are done."
        messagebox.showinfo("What's left?", body)

    def _setup_email_dialog(self) -> None:
        dlg = tk.Toplevel(self)
        dlg.title("Setup Email")
        dlg.geometry("420x220")
        dlg.transient(self)
        dlg.grab_set()

        ttk.Label(dlg, text="Gmail address:").pack(anchor=tk.W, padx=12, pady=(12, 0))
        user_entry = ttk.Entry(dlg, width=42)
        user_entry.pack(padx=12, pady=4)

        ttk.Label(dlg, text="App password (16 chars, hidden):").pack(anchor=tk.W, padx=12)
        pass_entry = ttk.Entry(dlg, width=42, show="*")
        pass_entry.pack(padx=12, pady=4)

        def save() -> None:
            user = user_entry.get().strip()
            pwd = pass_entry.get().strip()
            if not user or not pwd:
                messagebox.showerror(TITLE, "Enter Gmail address and app password.", parent=dlg)
                return
            try:
                email_setup.save_gmail_credentials(user, pwd)
                email_setup._mark_google_bat_only()
            except Exception as exc:
                messagebox.showerror(TITLE, f"Save failed: {type(exc).__name__}", parent=dlg)
                return
            pass_entry.delete(0, tk.END)
            dlg.destroy()
            self._refresh_checklist_ui()
            self._set_status("Email .env saved (password not shown)")
            messagebox.showinfo(
                TITLE,
                f"Local .env updated.\n\nAlso set same EMAIL_* on Render → Manual Deploy.\n\n"
                f"{email_setup.RENDER_HINT[:120]}…",
            )

        ttk.Button(dlg, text="Save", command=save).pack(pady=12)

    def _test_email(self) -> None:
        self._set_status("Sending test email…")

        def run() -> None:
            code = email_setup.run_test_send()
            msg = "Test email sent." if code == 0 else f"Test send exit code {code}"
            if code == 0:
                try:
                    tc.mark_done("email_test_done")
                except Exception:
                    pass
            self.after(0, lambda: self._finish_test_email(msg, code))

        threading.Thread(target=run, daemon=True).start()

    def _finish_test_email(self, msg: str, code: int) -> None:
        self._refresh_checklist_ui()
        self._set_status(msg)
        if code == 0:
            messagebox.showinfo(TITLE, f"{msg}\nRecipient: shieldinc850@gmail.com")
        else:
            messagebox.showwarning(TITLE, msg)

    def _open_reports(self) -> None:
        for path in REPORT_PATHS:
            cc.open_notepad(path)
        self._set_status("Opened readiness + team audit in Notepad")

    def _open_grok_sync(self) -> None:
        for path in GROK_PATHS:
            cc.open_notepad(path)
        self._set_status("Opened cursor_inbox + grok_outbox")

    def _thursday_burst(self) -> None:
        if not messagebox.askyesno(
            TITLE,
            "Run Thursday burst proof?\n\n"
            "100 paper fills at ~9:31 ET (bypasses TradingView).\n"
            "Only use when you intend burst testing.",
        ):
            return
        if BURST_BAT.exists():
            subprocess.Popen(
                ["cmd.exe", "/c", "start", "", str(BURST_BAT)],
                cwd=str(ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._set_status("Launched BURST-PAPER-100.bat")
        else:
            py = cc.python_exe()
            subprocess.Popen(
                [
                    str(py),
                    str(SCRIPTS / "burst_paper_fills.py"),
                    "--count",
                    "100",
                    "--interval",
                    "2",
                    "--wait-for-open",
                ],
                cwd=str(ROOT),
                creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
            )
            self._set_status("Launched burst_paper_fills.py in new console")

    def _stop_all(self, *, quiet: bool = False) -> None:
        STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
        STOP_FILE.write_text(
            f"stop requested {datetime.now(ET).isoformat()}\n",
            encoding="utf-8",
        )
        self._health_stop.set()
        for name, proc in list(self.procs.items()):
            cc.kill_worker(proc)
            cc.log_line(f"gui stopped: {name}")
        self.procs.clear()
        self._set_status("STOP ALL — workers killed, STOP file created")
        if not quiet:
            messagebox.showinfo(
                TITLE,
                "All team workers stopped.\nSTOP-REDUNDANT-TESTS.txt created.",
            )

    def _render_status(self) -> None:
        ok, detail = cc.fetch_health()
        self._set_status(f"{'health OK' if ok else 'health WARN'} | {detail}")
        messagebox.showinfo(
            "Render /health",
            f"{cc.HEALTH_URL}\n\n{'OK' if ok else 'WARN'}\n{detail}",
        )

    def _on_close(self) -> None:
        if self.procs and any(p.poll() is None for p in self.procs.values()):
            if not messagebox.askyesno(
                TITLE, "Workers still running. Stop all and exit?"
            ):
                return
        self._stop_all(quiet=True)
        self.destroy()


def main() -> int:
    app = CommandCenterApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
