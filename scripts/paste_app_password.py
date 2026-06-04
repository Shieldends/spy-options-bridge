#!/usr/bin/env python3
"""One-step GUI paste for Google App Password — never prints password to console."""

from __future__ import annotations

import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from setup_email_env import DEFAULT_TO, run_test_send, save_app_password_only  # noqa: E402

sys.path.insert(0, str(ROOT))
from email_alerts import _load_dotenv, _settings_from_env, normalize_smtp_password  # noqa: E402

FIXED_EMAIL = DEFAULT_TO


def _mark_email_setup_done() -> None:
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(sys.executable)
    for item in ("email_setup_done", "google_app_password_via_setup_bat_only"):
        try:
            subprocess.run(
                [str(py), str(ROOT / "scripts" / "mark_todo_done.py"), "--item", item],
                cwd=str(ROOT),
                check=False,
            )
        except Exception:
            pass


def on_save(entry: tk.Entry, root: tk.Tk) -> None:
    pwd = normalize_smtp_password(entry.get())
    try:
        save_app_password_only(pwd, email_to=FIXED_EMAIL)
    except ValueError as exc:
        messagebox.showerror("Invalid password", str(exc), parent=root)
        return
    except OSError as exc:
        messagebox.showerror("Save failed", str(exc), parent=root)
        return

    entry.delete(0, tk.END)
    _mark_email_setup_done()

    rc = run_test_send()
    if rc == 0:
        messagebox.showinfo(
            "PASS",
            f"App password saved for {FIXED_EMAIL}.\n"
            "Test email sent — check inbox/spam for [SPY Command Center].",
            parent=root,
        )
    else:
        messagebox.showerror(
            "FAIL",
            "Password saved but test email failed.\n\n"
            "Use a NEW 16-char Google App Password (Mail type).\n"
            "535 BadCredentials = wrong password or normal Gmail password.\n\n"
            "Check inbox/spam after fixing, or run TEST-EMAIL-NOW.bat.",
            parent=root,
        )
    root.destroy()


def main() -> int:
    root = tk.Tk()
    root.title("Paste Google App Password")
    root.resizable(False, False)

    _load_dotenv()
    old_len = len(_settings_from_env().get("smtp_password", ""))
    if old_len and old_len != 16:
        messagebox.showwarning(
            "Wrong saved password",
            f"Your .env has {old_len} characters (need exactly 16).\n\n"
            "Paste ONLY the 16-letter App Password from Google\n"
            "(not your normal Gmail password, not the label 'SPY Mail').",
            parent=root,
        )

    tk.Label(
        root,
        text="Paste 16-char Google App Password here, click SAVE",
        font=("Segoe UI", 11),
        wraplength=440,
        justify="center",
    ).pack(padx=20, pady=(18, 6))

    tk.Label(
        root,
        text=f"SMTP_USER / EMAIL_FROM / EMAIL_TO: {FIXED_EMAIL}",
        font=("Segoe UI", 9),
        fg="#333333",
    ).pack(pady=(0, 10))

    entry = tk.Entry(root, show="*", font=("Consolas", 16), width=22, justify="center")
    entry.pack(padx=20, pady=8)
    entry.focus_set()

    show_var = tk.BooleanVar(value=False)

    def toggle_show() -> None:
        entry.config(show="" if show_var.get() else "*")

    tk.Checkbutton(root, text="Show password", variable=show_var, command=toggle_show).pack(
        pady=4
    )

    tk.Button(
        root,
        text="SAVE",
        font=("Segoe UI", 11, "bold"),
        width=14,
        command=lambda: on_save(entry, root),
    ).pack(pady=(10, 18))

    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
