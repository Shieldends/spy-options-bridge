#!/usr/bin/env python3
"""Poll Gmail IMAP for [SPY Command Center] replies → operator grant / pending approval.

Requires EMAIL_ENABLED + SMTP_USER + SMTP_PASSWORD in .env (same Gmail app password as SMTP).
Run once:  python scripts/email_command_listener.py --once
Loop:     python scripts/email_command_listener.py --loop
"""

from __future__ import annotations

import argparse
import email
import imaplib
import json
import os
import sys
import time
from email.header import decode_header
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import email_approval as erg  # noqa: E402
from email_alerts import _load_dotenv, normalize_smtp_password  # noqa: E402


def _decode_mime_header(raw: str | None) -> str:
    if not raw:
        return ""
    parts: list[str] = []
    for chunk, enc in decode_header(raw):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            parts.append(str(chunk))
    return "".join(parts)


def _env_imap_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    _load_dotenv()
    ec = erg.email_command_cfg(cfg)
    user = (os.getenv("SMTP_USER") or os.getenv("EMAIL_FROM") or "").strip()
    password = normalize_smtp_password(os.getenv("SMTP_PASSWORD", ""))
    return {
        "host": ec.get("imap_host") or os.getenv("IMAP_HOST", "imap.gmail.com"),
        "port": int(ec.get("imap_port") or os.getenv("IMAP_PORT", "993")),
        "user": user,
        "password": password,
        "mailbox": ec.get("imap_mailbox", "INBOX"),
    }


def load_processed_uids(cfg: dict[str, Any]) -> set[str]:
    path = erg.processed_uids_path(cfg)
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        uids = data.get("uids") or []
        return {str(u) for u in uids}
    except (json.JSONDecodeError, OSError):
        return set()


def save_processed_uids(cfg: dict[str, Any], uids: set[str]) -> None:
    path = erg.processed_uids_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    trimmed = sorted(uids)[-500:]
    path.write_text(json.dumps({"uids": trimmed}, indent=2), encoding="utf-8")


def _extract_body(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    if not payload:
        return ""
    charset = msg.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def poll_inbox_once(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    cfg = cfg or erg.load_config()
    ec = erg.email_command_cfg(cfg)
    if not ec.get("enabled", True):
        return [{"ok": False, "messages": ["email_command disabled in operator_protocol.yaml"]}]

    imap_cfg = _env_imap_settings(cfg)
    if not imap_cfg["user"] or not imap_cfg["password"]:
        return [{"ok": False, "messages": ["IMAP not configured — set SMTP_USER + SMTP_PASSWORD in .env"]}]

    processed = load_processed_uids(cfg)
    results: list[dict[str, Any]] = []
    prefix = str(ec.get("subject_prefix", "[SPY Command Center]"))

    mail = imaplib.IMAP4_SSL(imap_cfg["host"], imap_cfg["port"])
    try:
        mail.login(imap_cfg["user"], imap_cfg["password"])
        mail.select(imap_cfg["mailbox"])
        status, data = mail.search(None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            return results

        for uid in data[0].split():
            uid_s = uid.decode() if isinstance(uid, bytes) else str(uid)
            if uid_s in processed:
                continue
            status, msg_data = mail.fetch(uid, "(RFC822)")
            if status != "OK" or not msg_data:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            subject = _decode_mime_header(msg.get("Subject"))
            from_hdr = _decode_mime_header(msg.get("From"))
            body = _extract_body(msg)
            if prefix.lower() not in subject.lower():
                processed.add(uid_s)
                continue
            result = erg.process_reply(from_addr=from_hdr, subject=subject, body=body, cfg=cfg)
            result["uid"] = uid_s
            results.append(result)
            mail.store(uid, "+FLAGS", "\\Seen")
            processed.add(uid_s)
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    save_processed_uids(cfg, processed)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Email command listener (IMAP replies)")
    parser.add_argument("--once", action="store_true", help="Poll inbox once and exit")
    parser.add_argument("--loop", action="store_true", help="Poll until interrupted")
    parser.add_argument("--status", action="store_true", help="Print pending approval summary")
    args = parser.parse_args(argv)
    cfg = erg.load_config()
    erg.ensure_email_command_control_doc(cfg)
    ec = erg.email_command_cfg(cfg)

    if args.status:
        print(erg.pending_summary(cfg))
        grant = erg.og.read_grant(cfg)
        ok, reason = erg.og.grant_status(grant)
        print(f"operator grant: {'active' if ok else reason}")
        return 0

    if not args.once and not args.loop:
        parser.error("specify --once, --loop, or --status")

    interval = int(ec.get("poll_interval_sec", 120))

    def run_pass() -> None:
        results = poll_inbox_once(cfg)
        for res in results:
            subj = res.get("subject", "")
            msgs = "; ".join(res.get("messages") or [])
            print(f"{'OK' if res.get('ok') else 'SKIP'} | {subj} | {msgs}")

    if args.once:
        run_pass()
        return 0

    print(f"Email command listener — poll every {interval}s (Ctrl+C to stop)")
    while True:
        run_pass()
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
