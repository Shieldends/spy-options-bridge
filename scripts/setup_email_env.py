#!/usr/bin/env python3
"""Interactive local .env setup for Gmail SMTP alerts (no secrets printed or committed)."""

from __future__ import annotations

import os
import subprocess
import sys
from getpass import getpass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
AUTOMATION_DOC = Path(r"C:\Users\Shiel\Desktop\AUTOMATION-SETUP.txt")
DEFAULT_TO = "shieldinc850@gmail.com"
RENDER_HINT = (
    "Render dashboard → spy-options-bridge → Environment:\n"
    "  EMAIL_ENABLED=true\n"
    "  SMTP_HOST=smtp.gmail.com\n"
    "  SMTP_PORT=587\n"
    "  SMTP_USER=<your Gmail>\n"
    "  SMTP_PASSWORD=<16-char app password>\n"
    "  EMAIL_FROM=<same Gmail>\n"
    f"  EMAIL_TO={DEFAULT_TO}\n"
    "Then Manual Deploy (do not paste passwords in chat)."
)


def load_env_lines() -> list[str]:
    if not ENV_PATH.exists():
        return []
    return ENV_PATH.read_text(encoding="utf-8").splitlines()


def parse_env(lines: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def upsert_env(updates: dict[str, str]) -> None:
    lines = load_env_lines()
    keys_done: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in updates:
                new_lines.append(f"{k}={updates[k]}")
                keys_done.add(k)
                continue
        new_lines.append(line.rstrip("\n"))
    for k, v in updates.items():
        if k not in keys_done:
            if new_lines and new_lines[-1].strip():
                new_lines.append("")
            new_lines.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def open_automation_doc() -> None:
    if AUTOMATION_DOC.exists():
        if sys.platform == "win32":
            os.startfile(str(AUTOMATION_DOC))  # noqa: S606
        else:
            print(f"Open: {AUTOMATION_DOC}")
    else:
        print(f"(Missing) {AUTOMATION_DOC}")


def _mark_google_bat_only() -> None:
    """Checklist: app password entered locally via bat, not chat."""
    try:
        scripts = Path(__file__).resolve().parent
        sys.path.insert(0, str(scripts))
        import todo_checklist as tc  # noqa: E402

        tc.mark_done("google_app_password_via_setup_bat_only")
        tc.mark_done("email_setup_done")
    except Exception:
        pass


def save_app_password_only(smtp_pass: str, *, email_to: str = DEFAULT_TO) -> None:
    """GUI path: inbox fixed to DEFAULT_TO — user only types app password."""
    save_gmail_credentials(DEFAULT_TO, smtp_pass, email_from=DEFAULT_TO, email_to=email_to)


def save_gmail_credentials(
    smtp_user: str,
    smtp_pass: str,
    *,
    email_from: str | None = None,
    email_to: str = DEFAULT_TO,
) -> None:
    """Write Gmail SMTP vars to local .env (caller must not log password)."""
    smtp_user = smtp_user.strip()
    smtp_pass = smtp_pass.strip().replace(" ", "")
    if not smtp_user or not smtp_pass:
        raise ValueError("Gmail address and app password are required")
    email_from = (email_from or smtp_user).strip() or smtp_user
    upsert_env(
        {
            "EMAIL_ENABLED": "true",
            "SMTP_HOST": "smtp.gmail.com",
            "SMTP_PORT": "587",
            "SMTP_USER": smtp_user,
            "SMTP_PASSWORD": smtp_pass,
            "EMAIL_FROM": email_from,
            "EMAIL_TO": email_to.strip() or DEFAULT_TO,
        }
    )


def run_test_send() -> int:
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(sys.executable)
    proc = subprocess.run(
        [str(py), str(ROOT / "scripts" / "remote_status_email.py"), "--test"],
        cwd=str(ROOT),
    )
    return proc.returncode


def main() -> int:
    print("=" * 60)
    print("SETUP EMAIL AUTOMATION — local .env only (never commit passwords)")
    print("=" * 60)
    open_automation_doc()
    print("\nGmail: enable 2-Step Verification → App passwords → Mail → copy 16 characters.\n")

    current = parse_env(load_env_lines())
    default_user = current.get("SMTP_USER") or current.get("EMAIL_FROM") or ""
    smtp_user = input(f"Gmail address (SMTP_USER) [{default_user or 'you@gmail.com'}]: ").strip()
    if not smtp_user:
        smtp_user = default_user or "you@gmail.com"

    smtp_pass = getpass("Gmail app password (16 chars, hidden): ").strip().replace(" ", "")
    if not smtp_pass:
        print("No password entered — aborting .env update.")
        return 1

    email_from = input(f"EMAIL_FROM [{smtp_user}]: ").strip() or smtp_user
    email_to = input(f"EMAIL_TO [{DEFAULT_TO}]: ").strip() or DEFAULT_TO

    upsert_env(
        {
            "EMAIL_ENABLED": "true",
            "SMTP_HOST": "smtp.gmail.com",
            "SMTP_PORT": "587",
            "SMTP_USER": smtp_user,
            "SMTP_PASSWORD": smtp_pass,
            "EMAIL_FROM": email_from,
            "EMAIL_TO": email_to,
        }
    )
    print(f"\nUpdated {ENV_PATH} (SMTP_PASSWORD=*** not shown).")
    _mark_google_bat_only()
    print("\n--- Also set the SAME vars on Render ---\n")
    print(RENDER_HINT)

    ans = input("\nSend test email now? [Y/n]: ").strip().lower()
    if ans in ("", "y", "yes"):
        return run_test_send()
    print("Run Desktop\\TEST-EMAIL-NOW.bat when ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
