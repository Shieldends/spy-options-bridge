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
TRIGGER_PROOF = DESKTOP / "TRIGGER-CHAIN-PROOF.txt"
TRIGGER_PROOF_BAT = DESKTOP / "RUN-TRIGGER-CHAIN-PROOF.bat"
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
APP_NAME = "SPY Command Center 2.0"
TITLE = APP_NAME
USER_EMAIL = email_setup.DEFAULT_TO
CC_VERSION = "2.0"
GUARDIAN_JOURNAL = DESKTOP / f"LIVE-SESSION-JOURNAL-{datetime.now(ET).strftime('%Y-%m-%d')}.txt"
CC_LOG = DESKTOP / "COMMAND-CENTER-LOG.txt"


class CommandCenterApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(TITLE)
        self.geometry("560x720")
        self.minsize(480, 600)
        cc._load_dotenv()

        self.procs: dict[str, subprocess.Popen[bytes]] = {}
        self._last_worker_spawn: dict[str, float] = {}
        self._health_stop = threading.Event()
        self._health_thread: threading.Thread | None = None
        self._check_vars: dict[str, tk.BooleanVar] = {}
        self._status_var = tk.StringVar(value="Starting… checking team and bridge")
        self._email_approval_var = tk.StringVar(value="Email approval: idle")
        self._live_banner_var = tk.StringVar(value="LIVE RUN: checking…")
        self._bridge_var = tk.StringVar(value="Bridge: …")
        self._guardian_var = tk.StringVar(value="Guardian: …")
        self._workers_var = tk.StringVar(value="Workers: …")
        self._handoff_var = tk.StringVar(value="Team sync: …")

        self.configure(bg="#f4f4f4")
        tc.ensure_live_defaults()
        tc.set_user_prefs(email=True, burst=True)
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(500, self._refresh_checklist_ui)
        self.after(600, self._bootstrap_team_state)
        self.after(800, self._refresh_email_approval_status)
        self.after(30000, self._schedule_email_approval_refresh)
        self.after(15000, self._schedule_banner_refresh)

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
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        header = tk.Frame(self, bg="#1e293b", height=52)
        header.pack(fill=tk.X)
        tk.Label(
            header,
            text=f"{APP_NAME}  ·  Guardian supervises PC helpers",
            font=("Segoe UI", 12, "bold"),
            fg="#f8fafc",
            bg="#1e293b",
        ).pack(pady=12)

        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        live = ttk.Frame(notebook, padding=10)
        team = ttk.Frame(notebook, padding=10)
        actions = ttk.Frame(notebook, padding=10)
        notebook.add(live, text="Live")
        notebook.add(team, text="Team")
        notebook.add(actions, text="Actions")

        self._banner_label = tk.Label(
            live,
            textvariable=self._live_banner_var,
            font=("Segoe UI", 15, "bold"),
            fg="#0a5",
            bg="#f4f4f4",
            wraplength=500,
            justify=tk.CENTER,
        )
        self._banner_label.pack(fill=tk.X, pady=(0, 10))

        for var in (self._bridge_var, self._guardian_var, self._workers_var):
            tk.Label(
                live,
                textvariable=var,
                font=("Segoe UI", 10),
                fg="#334155",
                bg="#f4f4f4",
                anchor=tk.W,
                justify=tk.LEFT,
                wraplength=500,
            ).pack(fill=tk.X, pady=2)

        tk.Label(live, text="Session journal (Guardian)", font=("Segoe UI", 9, "bold"), bg="#f4f4f4").pack(
            anchor=tk.W, pady=(10, 4)
        )
        self._journal_text = tk.Text(live, height=10, wrap=tk.WORD, font=("Consolas", 9), state=tk.DISABLED)
        self._journal_text.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            team,
            textvariable=self._handoff_var,
            font=("Segoe UI", 10),
            wraplength=500,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 8))
        tk.Label(
            team,
            textvariable=self._email_approval_var,
            font=("Segoe UI", 10),
            fg="#630",
            wraplength=500,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 8))

        for label, cmd in (
            ("Open Grok + Cursor sync", self._open_grok_sync),
            ("Open readiness reports", self._open_reports),
            ("What's left?", self._whats_left),
        ):
            ttk.Button(team, text=label, command=cmd).pack(fill=tk.X, pady=4)

        ttk.Separator(team).pack(fill=tk.X, pady=8)
        for key in tc.DEFAULT_ITEMS:
            var = tk.BooleanVar(value=False)
            self._check_vars[key] = var
            ttk.Checkbutton(
                team,
                text=tc.LABELS.get(key, key),
                variable=var,
                command=lambda k=key: self._on_check_toggle(k),
            ).pack(anchor=tk.W, pady=2)

        primary: list[tuple[str, callable]] = [
            ("Start Guardian", self._start_guardian),
            ("Stop Guardian", self._stop_guardian),
            ("EOD report now", self._run_eod_report),
            ("Stop all workers", self._stop_all),
            ("Render status", self._render_status),
        ]
        for label, cmd in primary:
            ttk.Button(actions, text=label, command=cmd).pack(fill=tk.X, pady=6, ipady=4)

        ttk.Label(actions, text="More (email / tests)", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W, pady=(12, 4))
        more = ttk.Frame(actions)
        more.pack(fill=tk.X)
        for label, cmd in (
            ("Setup email", self._setup_email_dialog),
            ("Test email", self._test_permission_email),
            ("Request approval", self._request_deploy_email_approval),
            ("Check email replies", self._check_email_replies),
            ("Email latest report", self._email_latest_report),
            ("Operator grant", self._operator_grant_session),
        ):
            ttk.Button(more, text=label, command=cmd).pack(fill=tk.X, pady=2)

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

    def _guardian_on(self) -> bool:
        try:
            import cc_guardian_ctl as gctl  # noqa: E402

            return gctl.guardian_running()
        except Exception:
            return False

    def _team_running(self) -> bool:
        """READY when Guardian is on or core workers are up."""
        if self._guardian_on():
            return True
        if cc.team_ready_for_display():
            return True
        return bool(self.procs) and any(p.poll() is None for p in self.procs.values())

    def _team_status_label(self) -> tuple[str, str]:
        """Banner text and tk label foreground color."""
        if cc.team_ready_for_display():
            if cc._market_session_open_now():
                return "READY — live session (redundant tests pause at 9:30 ET)", "#0a5"
            return "READY — team monitoring (TV → Render → Alpaca)", "#0a5"
        status = cc.team_worker_status()
        n = sum(1 for ok in status.values() if ok)
        if n > 0:
            missing = []
            for s, ok in status.items():
                if ok:
                    continue
                if s == "redundant_test_loop.py" and not cc.redundant_expected_up():
                    continue
                missing.append(s.replace("_loop.py", "").replace(".py", "").replace("_", " "))
            if not missing and status.get("dual_sync_loop.py") and status.get("bridge_keepalive.py"):
                return "READY — team monitoring (TV → Render → Alpaca)", "#0a5"
            need = ", ".join(missing) if missing else "redundant test"
            return f"PARTIAL — click START TEAM ({n}/3 up; need {need})", "#b60"
        return "OFF — click START TEAM once to start monitoring", "#a00"

    def _update_live_banner(self) -> None:
        text, color = self._team_status_label()
        self._live_banner_var.set(text)
        self._banner_label.configure(fg=color)
        data = tc.load_checklist()
        parts: list[str] = []
        if data.get("user_wants_email", True):
            parts.append("Email alerts ON")
        if data.get("user_wants_burst", True):
            parts.append("Burst 9:31 ET ON")
        grant_ok = (DESKTOP / "OPERATOR-GRANT.json").is_file()
        parts.append("Operator grant OK" if grant_ok else "No operator grant (ARM or grant button)")
        parts.append(self._trigger_proof_hint())
        self._prefs_var.set(" · ".join(parts))

    def _trigger_proof_hint(self) -> str:
        if not TRIGGER_PROOF.is_file():
            return "Trigger proof: not run"
        try:
            for line in TRIGGER_PROOF.read_text(encoding="utf-8").splitlines():
                if line.startswith("PASS="):
                    return f"Trigger proof {line.strip()}"
            mtime = datetime.fromtimestamp(TRIGGER_PROOF.stat().st_mtime, tz=ET).strftime(
                "%H:%M ET"
            )
            return f"Trigger proof @ {mtime}"
        except OSError:
            return "Trigger proof: unreadable"

    def _open_trigger_proof(self) -> None:
        if not TRIGGER_PROOF.is_file():
            self._set_status("Run RUN TRIGGER PROOF first")
            messagebox.showinfo(
                TITLE,
                "No report yet.\n\nDouble-click Desktop\\RUN-TRIGGER-CHAIN-PROOF.bat\n"
                "or click RUN TRIGGER PROOF here.",
            )
            return
        subprocess.Popen(["notepad.exe", str(TRIGGER_PROOF)])
        self._set_status(f"Opened {TRIGGER_PROOF.name}")

    def _run_trigger_proof(self) -> None:
        if not TRIGGER_PROOF_BAT.is_file():
            self._set_status("Missing RUN-TRIGGER-CHAIN-PROOF.bat on Desktop")
            return
        self._set_status("Running trigger chain proof…")
        subprocess.Popen(
            ["cmd", "/c", "start", "/wait", str(TRIGGER_PROOF_BAT)],
            cwd=str(DESKTOP),
        )
        self.after(2000, self._update_live_banner)
        self._set_status("Trigger proof finished — open TRIGGER PROOF REPORT")

    def _schedule_banner_refresh(self) -> None:
        self._reconcile_team_helpers()
        self._refresh_live_panel()
        self._update_live_banner()
        self.after(15000, self._schedule_banner_refresh)

    def _tail_file(self, path: Path, n: int = 12) -> str:
        if not path.is_file():
            return f"(no {path.name} yet)"
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:]) if lines else "(empty)"

    def _refresh_live_panel(self) -> None:
        ok, detail = cc.fetch_health()
        self._bridge_var.set(f"Bridge: {'OK' if ok else 'WARN'} | {detail}")
        try:
            import cc_guardian_ctl as gctl  # noqa: E402

            self._guardian_var.set(f"Guardian: {gctl.status_line()}")
        except Exception as exc:
            self._guardian_var.set(f"Guardian: error ({type(exc).__name__})")
        status = cc.team_worker_status()
        n = sum(1 for v in status.values() if v)
        self._workers_var.set(f"Workers: {n}/3 core helpers ({status})")
        try:
            from live_session_guardian import journal_path  # noqa: E402

            jpath = journal_path()
        except Exception:
            jpath = GUARDIAN_JOURNAL
        text = self._tail_file(jpath, 14)
        self._journal_text.configure(state=tk.NORMAL)
        self._journal_text.delete("1.0", tk.END)
        self._journal_text.insert(tk.END, text)
        self._journal_text.configure(state=tk.DISABLED)
        inbox = SYNC_DIR / "cursor_inbox.md"
        if inbox.is_file():
            lines = inbox.read_text(encoding="utf-8", errors="replace").splitlines()
            handoff = next((ln for ln in reversed(lines) if "handoff" in ln.lower() or ln.startswith("### [")), "")
            self._handoff_var.set(handoff[:200] if handoff else "Team sync: (no recent handoff line)")

    def _reconcile_team_helpers(self) -> None:
        """Dedupe duplicate helpers only — auto-respawn fights console supervisor (20min loops)."""
        self._prune_dead_procs()
        if not (DESKTOP / "OPERATOR-GRANT.json").is_file():
            return
        try:
            import dedupe_spy_workers as dedupe  # noqa: E402

            dupes = [s for s in cc.TEAM_WORKER_SCRIPTS if len(dedupe.pids_for_script(s)) > 1]
            if dupes:
                cc.dedupe_worker_duplicates_only()
                cc.log_line(f"GUI reconcile deduped: {', '.join(dupes)}")
        except Exception:
            pass

    def _prune_dead_procs(self) -> None:
        dead = [name for name, p in self.procs.items() if p.poll() is not None]
        for name in dead:
            del self.procs[name]

    def _bootstrap_team_state(self) -> None:
        """On open: prefer Guardian supervisor (CC 2.0)."""
        cc.stop_stale_console_supervisors()
        self._prune_dead_procs()
        grant = DESKTOP / "OPERATOR-GRANT.json"
        if grant.is_file() and not self._guardian_on():
            self._start_guardian(quiet=True)
        if self._team_running():
            self._set_status("Guardian / team active — this window is your desk")
            self._start_health_loop()
        else:
            self._set_status("Actions tab → Start Guardian (recommended)")
        self._refresh_live_panel()
        self._update_live_banner()

    def _start_guardian(self, *, quiet: bool = False) -> None:
        cc.stop_stale_console_supervisors()
        try:
            import cc_guardian_ctl as gctl  # noqa: E402

            ok, msg = gctl.start_guardian()
        except Exception as exc:
            ok, msg = False, f"{type(exc).__name__}"
        self._set_status(msg)
        self._refresh_live_panel()
        self._update_live_banner()
        self._start_health_loop()
        if ok and not quiet:
            messagebox.showinfo(TITLE, f"{msg}\n\nMACD path uses Render — Guardian babysits this PC only.")

    def _stop_guardian(self) -> None:
        try:
            import cc_guardian_ctl as gctl  # noqa: E402

            ok, msg = gctl.stop_guardian()
        except Exception as exc:
            ok, msg = False, str(exc)
        self._set_status(msg)
        self._refresh_live_panel()
        self._update_live_banner()

    def _run_eod_report(self) -> None:
        self._set_status("Building EOD report…")
        py = cc.python_exe()
        proc = subprocess.run(
            [str(py), str(SCRIPTS / "eod_session_report.py"), "--email"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if proc.returncode == 0:
            self._set_status("EOD report written to Desktop (+ email if SMTP ok)")
            day = datetime.now(ET).strftime("%Y-%m-%d")
            path = DESKTOP / f"EOD-SESSION-REPORT-{day}.txt"
            if path.is_file():
                cc.open_notepad(path)
        else:
            self._set_status(f"EOD report failed (code {proc.returncode})")

    def _start_team(self, *, quiet: bool = False, auto: bool = False) -> None:
        """Legacy name — CC 2.0 uses Guardian."""
        self._start_guardian(quiet=quiet or auto)

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

    def _start_health_loop(self) -> None:
        self._health_stop.clear()
        if self._health_thread and self._health_thread.is_alive():
            return

        def loop() -> None:
            while not self._health_stop.wait(cc.INTERVAL_SEC):
                ok, detail = cc.fetch_health()
                msg = f"health {'OK' if ok else 'WARN'} | {detail}"
                self.after(0, lambda m=msg: self._set_status(m))
                self.after(0, self._update_live_banner)

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
        cc.stop_team_workers()
        self._update_live_banner()
        self._set_status("STOP ALL — helpers stopped, STOP file created")
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
        if self._guardian_on() or self._team_running():
            if messagebox.askyesno(
                TITLE,
                "Guardian or workers still running.\n\n"
                "Stop Guardian + workers and close? (No = leave Guardian in background.)",
            ):
                self._stop_guardian()
                self._stop_all(quiet=True)
        cc.release_gui_lock()
        self.destroy()


def _one_dialog(title: str, msg: str, *, kind: str = "info") -> None:
    """Single popup (one Tk root) — avoids stacked or duplicate dialog boxes."""
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    try:
        if kind == "error":
            messagebox.showerror(title, msg, parent=root)
        else:
            messagebox.showinfo(title, msg, parent=root)
    finally:
        root.destroy()


def main() -> int:
    cc.consolidate_to_one_gui()
    if not cc.try_acquire_gui_lock():
        return 0
    try:
        app = CommandCenterApp()
        app.mainloop()
        cc.release_gui_lock()
        return 0
    except Exception as exc:
        crash = DESKTOP / "COMMAND-CENTER-CRASH.txt"
        try:
            import traceback

            crash.write_text(traceback.format_exc(), encoding="utf-8")
        except OSError:
            pass
        try:
            _one_dialog(
                TITLE,
                f"Command Center closed due to an error.\n\n{type(exc).__name__}: {exc}\n\n"
                f"Details: {crash}",
                kind="error",
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
