#!/usr/bin/env python3
"""SPY Live Command Center — tkinter app (stdlib). UX layer on command_center workers."""

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
APP_NAME = "SPY Live Command Center"
TITLE = APP_NAME
USER_EMAIL = email_setup.DEFAULT_TO


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
        self._email_approval_var = tk.StringVar(value="Email approval: idle")
        self._live_banner_var = tk.StringVar(value="LIVE RUN: checking…")
        self._prefs_var = tk.StringVar(value="")

        tc.ensure_live_defaults()
        tc.set_user_prefs(email=True, burst=True)
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(500, self._refresh_checklist_ui)
        self.after(600, self._update_live_banner)
        self.after(800, self._refresh_email_approval_status)
        self.after(30000, self._schedule_email_approval_refresh)

    def _schedule_email_approval_refresh(self) -> None:
        self._refresh_email_approval_status()
        self.after(30000, self._schedule_email_approval_refresh)

    def _refresh_email_approval_status(self) -> None:
        try:
            import email_approval as erg  # noqa: E402

            summary = erg.pending_summary()
            if summary.startswith("awaiting"):
                self._email_approval_var.set(f"⏳ {summary}")
            elif summary.startswith("pending expired"):
                self._email_approval_var.set(f"⚠ {summary}")
            else:
                grant = erg.og.read_grant(erg.load_config())
                ok, reason = erg.og.grant_status(grant)
                if ok:
                    self._email_approval_var.set("Email approval: operator grant active")
                else:
                    self._email_approval_var.set(f"Email approval: idle ({reason})")
        except Exception as exc:
            self._email_approval_var.set(f"Email approval: status error ({type(exc).__name__})")

    def _build_ui(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        main = ttk.Frame(notebook, padding=8)
        checklist = ttk.Frame(notebook, padding=8)
        help_tab = ttk.Frame(notebook, padding=12)
        notebook.add(main, text="Commands")
        notebook.add(checklist, text="Checklist")
        notebook.add(help_tab, text="Help")

        tk.Label(
            main,
            text=APP_NAME,
            font=("Segoe UI", 14, "bold"),
            wraplength=520,
            justify=tk.CENTER,
        ).grid(row=0, column=0, columnspan=2, pady=(0, 4), sticky="ew")

        tk.Label(
            main,
            textvariable=self._live_banner_var,
            font=("Segoe UI", 16, "bold"),
            fg="#0a5",
            wraplength=520,
            justify=tk.CENTER,
        ).grid(row=1, column=0, columnspan=2, pady=(4, 4), sticky="ew")

        tk.Label(
            main,
            textvariable=self._prefs_var,
            font=("Segoe UI", 10),
            fg="#036",
            wraplength=520,
            justify=tk.CENTER,
        ).grid(row=2, column=0, columnspan=2, pady=(0, 8), sticky="ew")

        tk.Label(
            main,
            textvariable=self._email_approval_var,
            font=("Segoe UI", 10, "bold"),
            fg="#630",
            wraplength=520,
            justify=tk.CENTER,
        ).grid(row=3, column=0, columnspan=2, pady=(0, 6), sticky="ew")

        buttons: list[tuple[str, callable]] = [
            ("START TEAM", self._start_team),
            ("WHAT'S LEFT?", self._whats_left),
            ("OPTIONAL: Setup Email", self._setup_email_dialog),
            ("SEND TEST + PERMISSION SAMPLE", self._test_permission_email),
            ("Request approval email", self._request_deploy_email_approval),
            ("CHECK EMAIL REPLIES", self._check_email_replies),
            ("EMAIL ME LATEST REPORT", self._email_latest_report),
            ("OPEN REPORTS", self._open_reports),
            ("OPEN GROK SYNC", self._open_grok_sync),
            ("THURSDAY BURST (9:31 ET)", self._thursday_burst),
            ("STOP ALL", self._stop_all),
            ("RENDER STATUS", self._render_status),
            ("Operator: grant session", self._operator_grant_session),
        ]
        for idx, (label, cmd) in enumerate(buttons):
            row, col = divmod(idx + 4, 2)
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
            )
            cb.pack(anchor=tk.W, pady=4, padx=4)

        ttk.Button(
            checklist, text="Refresh checklist", command=self._refresh_checklist_ui
        ).pack(pady=6)

        help_text = (
            "SPY Live Command Center — the app (one window).\n\n"
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

    def _team_running(self) -> bool:
        return bool(self.procs) and any(p.poll() is None for p in self.procs.values())

    def _update_live_banner(self) -> None:
        if self._team_running():
            self._live_banner_var.set("LIVE RUN: READY")
        else:
            self._live_banner_var.set("NEEDS: click START TEAM")
        data = tc.load_checklist()
        parts: list[str] = []
        if data.get("user_wants_email", True):
            parts.append("Email alerts ON (you confirmed)")
        if data.get("user_wants_burst", True):
            parts.append("Burst 9:31 ET ON (you confirmed)")
        self._prefs_var.set("YOU WANT: " + " | ".join(parts) if parts else "")

    def _refresh_checklist_ui(self) -> None:
        tc.ensure_live_defaults()
        data = tc.load_checklist()
        for key, var in self._check_vars.items():
            var.set(bool(data["items"].get(key, False)))
        self._update_live_banner()

    def _on_check_toggle(self, key: str) -> None:
        done = self._check_vars[key].get()
        tc.set_item(key, done)
        tc.write_human_summary()
        self._set_status(f"Checklist: {tc.LABELS.get(key, key)} → {'done' if done else 'open'}")

    def _start_team(self) -> None:
        if self.procs and any(p.poll() is None for p in self.procs.values()):
            messagebox.showinfo(TITLE, "Team workers already running.")
            return
        auto_arm = DESKTOP / "OPERATOR-AUTO-ARM.txt"
        if auto_arm.is_file():
            try:
                import operator_gateway as og  # noqa: E402

                granted = og.try_auto_grant_from_marker()
                if granted:
                    self._set_status(f"Auto operator grant → {granted.name}")
            except Exception as exc:
                self._set_status(f"Auto grant skipped: {type(exc).__name__}")
        cc.mark_todo_items()
        self.procs = cc.spawn_all_workers()
        self._set_status("START TEAM — 3 workers spawned")
        self._update_live_banner()
        self._start_health_loop()
        self._email_team_started()
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
        before_path = DESKTOP / "BEFORE-MARKET-OPEN.txt"
        if before_path.exists():
            cc.open_notepad(before_path)
        lines = tc.format_user_live_lines(team_running=self._team_running())
        if lines:
            body = "You need (max 3):\n\n" + "\n".join(f"• {line}" for line in lines)
        else:
            body = (
                "Nothing required.\n\n"
                "Live at 9:30 with zero clicks if this window stayed open after START TEAM."
            )
        messagebox.showinfo("What's left?", body)

    def _setup_email_dialog(self) -> None:
        dlg = tk.Toplevel(self)
        dlg.title("Setup Email (1 field)")
        dlg.geometry("440x200")
        dlg.transient(self)
        dlg.grab_set()

        ttk.Label(
            dlg,
            text=f"From/To (fixed): {USER_EMAIL}",
            wraplength=400,
        ).pack(anchor=tk.W, padx=12, pady=(12, 0))
        ttk.Label(dlg, text="Gmail app password (16 chars, hidden):").pack(anchor=tk.W, padx=12)
        pass_entry = ttk.Entry(dlg, width=42, show="*")
        pass_entry.pack(padx=12, pady=4)

        def save() -> None:
            pwd = pass_entry.get().strip().replace(" ", "")
            if not pwd:
                messagebox.showerror(TITLE, "Enter app password.", parent=dlg)
                return
            if len(pwd) != 16:
                messagebox.showerror(
                    TITLE,
                    "App password must be exactly 16 characters (remove spaces from Google's display).",
                    parent=dlg,
                )
                return
            try:
                email_setup.save_app_password_only(pwd)
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
                "Local .env saved (not verified yet).\n\n"
                "Click SEND TEST + PERMISSION SAMPLE — you must see "
                "[SPY Command Center] in shieldinc850@gmail.com.\n\n"
                "If test fails: new Google App Password (16 chars), save again.",
            )

        ttk.Button(dlg, text="Save", command=save).pack(pady=12)

    def _email_team_started(self) -> None:
        cc._load_dotenv()
        sys.path.insert(0, str(SCRIPTS))
        import team_email as te  # noqa: E402

        def run() -> None:
            ok = te.notify_command_center_started()
            msg = "Startup email sent" if ok else "Startup email skipped (setup SMTP first)"
            self.after(0, lambda: self._set_status(msg))

        threading.Thread(target=run, daemon=True).start()

    def _email_latest_report(self) -> None:
        self._set_status("Emailing LIVE-RUN + FINAL audit…")
        cc._load_dotenv()
        sys.path.insert(0, str(SCRIPTS))
        import team_email as te  # noqa: E402

        def run() -> None:
            ok = te.send_latest_reports_email()
            msg = (
                f"Report email sent — check {USER_EMAIL}"
                if ok
                else "Report email failed — run OPTIONAL Setup Email first."
            )
            self.after(0, lambda: self._set_status(msg))
            if ok:
                self.after(0, lambda: messagebox.showinfo(TITLE, msg))
            else:
                self.after(0, lambda: messagebox.showwarning(TITLE, msg))

        threading.Thread(target=run, daemon=True).start()

    def _test_permission_email(self) -> None:
        self._set_status("Sending test + permission sample…")
        cc._load_dotenv()
        sys.path.insert(0, str(SCRIPTS))
        import team_email as te  # noqa: E402

        def run() -> None:
            ok_status, ok_perm = te.send_test_and_permission_sample()
            code = 0 if (ok_status or ok_perm) else 1
            msg = (
                "Status + permission sample sent — reply YES to the permission email."
                if code == 0
                else "Email failed — run OPTIONAL Setup Email first."
            )
            if code == 0:
                try:
                    tc.mark_done("email_test_done")
                except Exception:
                    pass
            self.after(0, lambda: self._finish_test_email(msg, code))

        threading.Thread(target=run, daemon=True).start()

    def _request_deploy_email_approval(self) -> None:
        if not messagebox.askyesno(
            TITLE,
            f"Email deploy approval request to {USER_EMAIL}?\n\n"
            "Reply YES or DEPLOY from that inbox to grant 12h operator session "
            "and write DEPLOY-APPROVED.txt on Desktop\\SPY-Command-Center.",
        ):
            return
        self._set_status("Sending deploy approval email…")
        cc._load_dotenv()
        sys.path.insert(0, str(SCRIPTS))
        import team_email as te  # noqa: E402

        def run() -> None:
            ok, pending_id = te.send_deploy_approval_request()
            if ok:
                msg = f"Deploy approval sent — awaiting email OK ({pending_id})"
            else:
                msg = "Deploy email failed — run OPTIONAL Setup Email first."
            self.after(0, lambda: self._set_status(msg))
            self.after(0, self._refresh_email_approval_status)
            if ok:
                self.after(
                    0,
                    lambda: messagebox.showinfo(
                        TITLE,
                        f"{msg}\n\nReply YES or DEPLOY to the permission email.",
                    ),
                )
            else:
                self.after(0, lambda: messagebox.showwarning(TITLE, msg))

        threading.Thread(target=run, daemon=True).start()

    def _check_email_replies(self) -> None:
        self._set_status("Checking inbox for Command Center replies…")
        cc._load_dotenv()
        sys.path.insert(0, str(SCRIPTS))

        def run() -> None:
            try:
                import email_command_listener as ecl  # noqa: E402
                import email_approval as erg  # noqa: E402

                results = ecl.poll_inbox_once()
                if not results:
                    msg = "No new permission replies (or IMAP not configured)."
                else:
                    parts = []
                    for res in results:
                        flag = "OK" if res.get("ok") else "skip"
                        parts.append(f"{flag}: {'; '.join(res.get('messages') or [])}")
                    msg = " | ".join(parts)
            except Exception as exc:
                msg = f"Email check failed: {type(exc).__name__}"
            self.after(0, lambda: self._set_status(msg))
            self.after(0, self._refresh_email_approval_status)
            self.after(0, lambda: messagebox.showinfo(TITLE, msg))

        threading.Thread(target=run, daemon=True).start()

    def _finish_test_email(self, msg: str, code: int) -> None:
        self._refresh_checklist_ui()
        self._set_status(msg)
        if code == 0:
            messagebox.showinfo(TITLE, f"{msg}\nInbox: {USER_EMAIL}")
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
        now = datetime.now(ET)
        schedule = (
            "Scheduled: click this button at 9:31 ET Thursday.\n\n"
            "100 paper fills (bypasses TradingView). You confirmed this test.\n\n"
            "Launch now only if market is open and you intend to run early."
        )
        if not messagebox.askyesno(TITLE, schedule + "\n\nLaunch burst now?"):
            self._set_status("Burst: waiting for 9:31 ET click")
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
        self._update_live_banner()
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

    def _operator_grant_session(self) -> None:
        if not messagebox.askyesno(
            TITLE,
            "Grant operator SESSION for 60 minutes?\n\n"
            "Writes Desktop\\OPERATOR-GRANT.json.\n"
            "Revoke: delete that file.",
        ):
            return
        py = ROOT / ".venv" / "Scripts" / "python.exe"
        gw = SCRIPTS / "operator_gateway.py"
        try:
            proc = subprocess.run(
                [str(py), str(gw), "--grant-tier", "session"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except OSError as exc:
            messagebox.showerror(TITLE, f"Grant failed: {exc}")
            return
        if proc.returncode != 0:
            messagebox.showerror(
                TITLE,
                (proc.stderr or proc.stdout or "grant failed")[:400],
            )
            return
        self._set_status("Operator session granted (60 min)")
        messagebox.showinfo(
            TITLE,
            "Grant written:\nC:\\Users\\Shiel\\Desktop\\OPERATOR-GRANT.json\n\n"
            "Revoke anytime: delete that file.",
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
