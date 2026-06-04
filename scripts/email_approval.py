#!/usr/bin/env python3
"""Email/SMS reply → operator grant + pending action (parser + Desktop file state).

IMAP polling: email_command_listener.py (standalone; not imported by command_center).
Writes: Desktop\\OPERATOR-GRANT.json, Desktop\\PENDING-ACTION.json
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "operator_protocol.yaml"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import operator_gateway as og  # noqa: E402

KEYWORD_PATTERN = re.compile(
    r"\b(YES|OK|APPROVE|GRANT|DEPLOY|STOP|NO|DENY|CANCEL)\b",
    re.IGNORECASE,
)
PENDING_ID_PATTERN = re.compile(
    r"PENDING-ID:\s*([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
QUOTE_MARKERS = (
    "-----original message-----",
    "----- forwarded message -----",
    "on ",
    " wrote:",
    "from:",
    "sent from",
)

DEFAULT_KEYWORD_MAP: dict[str, list[str]] = {
    "YES": ["grant_session", "approve_pending"],
    "OK": ["grant_session", "approve_pending"],
    "GRANT": ["grant_session", "approve_pending"],
    "APPROVE": ["approve_pending"],
    "DEPLOY": ["render_deploy_nudge", "grant_session", "approve_pending"],
    "STOP": ["stop_team"],
    "NO": ["deny_pending"],
    "DENY": ["deny_pending"],
    "CANCEL": ["deny_pending"],
}


def _config_path() -> Path:
    override = os.environ.get("OPERATOR_PROTOCOL_CONFIG")
    return Path(override) if override else CONFIG_PATH


def load_config() -> dict[str, Any]:
    with _config_path().open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def email_command_cfg(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("email_command") or {}


def desktop_folder(cfg: dict[str, Any]) -> Path:
    return Path(cfg["paths"]["user_root"]) / "Desktop"


def pending_path(cfg: dict[str, Any]) -> Path:
    ec = email_command_cfg(cfg)
    override = ec.get("pending_file")
    if override:
        return Path(override)
    return desktop_folder(cfg) / "PENDING-ACTION.json"


def processed_uids_path(cfg: dict[str, Any]) -> Path:
    ec = email_command_cfg(cfg)
    override = ec.get("processed_uids_file")
    if override:
        return Path(override)
    folder = Path(cfg["paths"]["command_center_folder"])
    return folder / "OPERATOR-EMAIL-PROCESSED.json"


def approval_markers_dir(cfg: dict[str, Any]) -> Path:
    return Path(cfg["paths"]["command_center_folder"])


def email_grant_minutes(cfg: dict[str, Any]) -> int:
    hours = float(email_command_cfg(cfg).get("email_reply_grant_hours", 12))
    return int(hours * 60)


def allowed_inbox(cfg: dict[str, Any]) -> str:
    return str(email_command_cfg(cfg).get("allowed_inbox", "shieldinc850@gmail.com")).lower()


def allowed_actions(cfg: dict[str, Any]) -> set[str]:
    raw = email_command_cfg(cfg).get("allowed_actions_from_email") or []
    return {str(x).strip() for x in raw}


def _keyword_map_key(key: Any) -> str:
    if key is True:
        return "YES"
    if key is False:
        return "NO"
    return str(key).upper()


def reply_keyword_map(cfg: dict[str, Any]) -> dict[str, list[str]]:
    ec = email_command_cfg(cfg)
    raw = ec.get("reply_keyword_map")
    if isinstance(raw, dict) and raw:
        return {_keyword_map_key(k): [str(a) for a in v] for k, v in raw.items()}
    return DEFAULT_KEYWORD_MAP


def normalize_reply_body(body: str) -> str:
    if not body:
        return ""
    lines: list[str] = []
    for line in body.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        low = stripped.lower()
        if any(m in low for m in QUOTE_MARKERS):
            break
        if stripped.startswith(">"):
            break
        lines.append(stripped)
    return "\n".join(lines).strip()


def extract_keywords(text: str) -> list[str]:
    found: list[str] = []
    for match in KEYWORD_PATTERN.finditer(text or ""):
        word = match.group(1).upper()
        if word not in found:
            found.append(word)
    return found


def extract_pending_id(subject: str, body: str) -> str | None:
    for blob in (subject or "", body or ""):
        m = PENDING_ID_PATTERN.search(blob)
        if m:
            return m.group(1)
    return None


def map_keywords_to_actions(keywords: list[str], cfg: dict[str, Any]) -> list[str]:
    allowed = allowed_actions(cfg)
    mapping = reply_keyword_map(cfg)
    actions: list[str] = []

    def add(action: str) -> None:
        if action in allowed and action not in actions:
            actions.append(action)

    if {"NO", "DENY", "CANCEL"} & {k.upper() for k in keywords}:
        for kw in ("NO", "DENY", "CANCEL"):
            if kw in keywords or kw.upper() in {x.upper() for x in keywords}:
                for action in mapping.get(kw, ["deny_pending"]):
                    add(action)
                return actions

    for kw in keywords:
        for action in mapping.get(kw.upper(), []):
            add(action)
    return actions


def load_pending_store(cfg: dict[str, Any]) -> dict[str, Any]:
    path = pending_path(cfg)
    if not path.is_file():
        return {"requests": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("requests"), list):
            return data
        if isinstance(data, dict) and data.get("id"):
            return {"requests": [data]}
    except (json.JSONDecodeError, OSError):
        pass
    return {"requests": []}


def save_pending_store(cfg: dict[str, Any], data: dict[str, Any]) -> Path:
    path = pending_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_pending_id(kind: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(3)
    return f"{kind}-{stamp}-{suffix}"


def create_pending_request(
    cfg: dict[str, Any],
    *,
    kind: str,
    title: str,
    detail: str = "",
    expires_hours: float = 24,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pending_id = _new_pending_id(kind)
    now = datetime.now(timezone.utc)
    entry: dict[str, Any] = {
        "id": pending_id,
        "kind": kind,
        "title": title,
        "detail": detail,
        "status": "pending",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=expires_hours)).isoformat(),
    }
    if extra:
        entry.update(extra)
    store = load_pending_store(cfg)
    store["requests"] = [r for r in store.get("requests") or [] if r.get("status") != "pending"]
    store["requests"].append(entry)
    save_pending_store(cfg, store)
    return entry


def find_pending_request(
    cfg: dict[str, Any],
    pending_id: str | None = None,
    *,
    status: str = "pending",
) -> dict[str, Any] | None:
    store = load_pending_store(cfg)
    requests = store.get("requests") or []
    if pending_id:
        for req in reversed(requests):
            if req.get("id") == pending_id and req.get("status") == status:
                return req
        return None
    for req in reversed(requests):
        if req.get("status") == status:
            return req
    return None


def _set_pending_status(
    cfg: dict[str, Any],
    pending_id: str,
    status: str,
    *,
    resolved_by: str = "email-reply",
) -> bool:
    store = load_pending_store(cfg)
    updated = False
    entry: dict[str, Any] | None = None
    for req in store.get("requests") or []:
        if req.get("id") == pending_id:
            req["status"] = status
            req["resolved_at"] = _now_iso()
            req["resolved_by"] = resolved_by
            entry = req
            updated = True
            break
    if updated:
        save_pending_store(cfg, store)
    return updated


def pending_is_expired(req: dict[str, Any]) -> bool:
    exp_raw = req.get("expires_at")
    if not exp_raw:
        return False
    try:
        exp = datetime.fromisoformat(str(exp_raw).replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) >= exp


def approve_pending(
    cfg: dict[str, Any],
    pending_id: str | None = None,
    *,
    resolved_by: str = "email-reply",
) -> tuple[bool, str]:
    req = find_pending_request(cfg, pending_id)
    if not req:
        return False, "no pending request"
    if pending_is_expired(req):
        _set_pending_status(cfg, req["id"], "expired", resolved_by=resolved_by)
        return False, "pending expired"
    _set_pending_status(cfg, req["id"], "approved", resolved_by=resolved_by)
    return True, req["id"]


def deny_pending(
    cfg: dict[str, Any],
    pending_id: str | None = None,
    *,
    resolved_by: str = "email-reply",
) -> tuple[bool, str]:
    req = find_pending_request(cfg, pending_id)
    if not req:
        return False, "no pending request"
    _set_pending_status(cfg, req["id"], "denied", resolved_by=resolved_by)
    return True, req["id"]


def write_email_grant(cfg: dict[str, Any], *, source: str = "email-reply") -> Path:
    minutes = email_grant_minutes(cfg)
    return og.write_grant("session", cfg, source=source, minutes=minutes)


def send_sms(message: str, *, settings: dict[str, Any] | None = None) -> bool:
    """Optional Twilio SMS; no-op when not configured (use email-to-SMS gateway)."""
    _ = settings
    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    from_num = os.getenv("TWILIO_FROM_NUMBER", "").strip()
    to_num = os.getenv("TWILIO_TO_NUMBER", "").strip()
    if not all((sid, token, from_num, to_num)):
        return False
    try:
        from twilio.rest import Client  # type: ignore[import-untyped]

        Client(sid, token).messages.create(body=message[:1400], from_=from_num, to=to_num)
        return True
    except Exception:
        return False


def ensure_email_command_control_doc(cfg: dict[str, Any] | None = None) -> Path:
    """Desktop doc: email reply + optional SMS-via-email gateway (no Twilio required)."""
    cfg = cfg or load_config()
    folder = Path(cfg["paths"]["command_center_folder"])
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "EMAIL-COMMAND-CONTROL.txt"
    inbox = email_command_cfg(cfg).get("allowed_inbox", "shieldinc850@gmail.com")
    hours = email_command_cfg(cfg).get("email_reply_grant_hours", 12)
    prefix = email_command_cfg(cfg).get("subject_prefix", "[SPY Command Center]")
    sms_gateway = email_command_cfg(cfg).get(
        "sms_email_gateway",
        "your_phone@txt.att.net",
    )
    lines = [
        "SPY Command Center — email / text reply control",
        "",
        f"Inbox (reply from): {inbox}",
        f"Subject must include: {prefix}",
        f"Approval requests use subject: {prefix} NEED APPROVAL - <action>",
        "",
        "Reply keywords (body or subject):",
        "  YES / OK / GRANT  → 12h operator grant + approve pending action",
        "  APPROVE           → approve pending only",
        "  DEPLOY            → deploy markers + grant + approve",
        "  STOP              → stop team workers (STOP-REDUNDANT-TESTS.txt)",
        "  NO / DENY / CANCEL → reject pending",
        "",
        "Keep PENDING-ID: <id> in the reply thread when answering approval email.",
        "",
        f"Grant file: {cfg['paths']['grant_file']}",
        f"Pending file: {pending_path(cfg)}",
        f"Grant duration (email): {hours} hours",
        "",
        "One-shot inbox poll (safe, does not start workers):",
        r"  C:\Users\Shiel\spy-options-bridge\launchers\CHECK-EMAIL-APPROVALS.bat",
        "",
        "SMS without Twilio (.env): use carrier email-to-SMS gateway.",
        f"  Example gateway address: {sms_gateway}",
        "  Send SMTP to that address; same YES/OK/DEPLOY/STOP keywords in body.",
        "  Optional: set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, TWILIO_TO_NUMBER",
        "",
        "Revoke operator anytime: delete Desktop\\OPERATOR-GRANT.json",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_marker(cfg: dict[str, Any], name: str, lines: list[str]) -> Path:
    folder = approval_markers_dir(cfg)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def apply_render_deploy_nudge(cfg: dict[str, Any], req: dict[str, Any] | None) -> Path:
    ts = _now_iso()
    lines = [
        f"approved_at={ts}",
        "action=render_manual_deploy",
        f"pending_id={req.get('id') if req else 'none'}",
        "next=Render dashboard → Manual Deploy (after git push if needed)",
        "url=https://dashboard.render.com/",
    ]
    deploy_marker = _write_marker(cfg, "DEPLOY-APPROVED.txt", lines)
    apply_git_push_reminder(cfg, req)
    return deploy_marker


def apply_git_push_reminder(cfg: dict[str, Any], req: dict[str, Any] | None) -> Path:
    ts = _now_iso()
    lines = [
        f"approved_at={ts}",
        "action=git_push_reminder",
        f"pending_id={req.get('id') if req else 'none'}",
        f"repo={cfg['paths']['bridge_root']}",
        "next=git push from spy-options-bridge then Render Manual Deploy",
    ]
    return _write_marker(cfg, "GIT-PUSH-APPROVED.txt", lines)


def apply_stop_team(cfg: dict[str, Any]) -> Path:
    stop_file = desktop_folder(cfg) / "STOP-REDUNDANT-TESTS.txt"
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    stop_file.write_text(f"stop requested via email {_now_iso()}\n", encoding="utf-8")
    return stop_file


def apply_cancel_orders(cfg: dict[str, Any]) -> tuple[bool, str]:
    if "cancel_orders" not in allowed_actions(cfg):
        return False, "cancel_orders not allowed from email"
    marker = _write_marker(
        cfg,
        "CANCEL-ORDERS-APPROVED.txt",
        [f"approved_at={_now_iso()}", "action=cancel_open_orders"],
    )
    grant = og.read_grant(cfg)
    ok_grant, reason = og.grant_status(grant)
    if not ok_grant:
        return True, f"marker written ({marker.name}); operator grant required ({reason})"
    script = Path(cfg["paths"]["bridge_root"]) / "scripts" / "cancel_open_orders.py"
    if not script.is_file():
        return True, f"marker written; script missing: {script.name}"
    rc = og.run_action("shell", f".venv\\Scripts\\python.exe scripts\\cancel_open_orders.py", cfg)
    return rc == 0, "cancel script executed" if rc == 0 else "cancel script failed (see audit log)"


def awaiting_email_ok(cfg: dict[str, Any] | None = None) -> bool:
    cfg = cfg or load_config()
    req = find_pending_request(cfg)
    return req is not None and not pending_is_expired(req)


def pending_summary(cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg or load_config()
    req = find_pending_request(cfg)
    if not req:
        return "no pending approval"
    if pending_is_expired(req):
        return f"pending expired: {req.get('id')}"
    return f"awaiting email OK: {req.get('title')} ({req.get('id')})"


def process_reply(
    *,
    from_addr: str,
    subject: str,
    body: str,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = cfg or load_config()
    result: dict[str, Any] = {
        "ok": False,
        "from": from_addr,
        "subject": (subject or "")[:120],
        "actions": [],
        "messages": [],
    }

    sender = (from_addr or "").lower()
    inbox = allowed_inbox(cfg)
    if inbox not in sender:
        result["messages"].append(f"sender not allowed (expected {inbox})")
        return result

    prefix = str(email_command_cfg(cfg).get("subject_prefix", "[SPY Command Center]"))
    subj_low = (subject or "").lower()
    approval_thread = (
        "need approval" in subj_low
        or "approval needed" in subj_low
        or "permission:" in subj_low
    )
    if prefix.lower() not in subj_low and not approval_thread:
        result["messages"].append("subject not a Command Center / approval thread")
        return result

    normalized = normalize_reply_body(body)
    keywords = extract_keywords(normalized)
    if not keywords:
        result["messages"].append("no recognized keywords (YES OK APPROVE GRANT DEPLOY STOP NO)")
        return result

    pending_id = extract_pending_id(subject, body)
    actions = map_keywords_to_actions(keywords, cfg)
    result["keywords"] = keywords
    result["actions"] = actions
    result["pending_id"] = pending_id

    if not actions:
        result["messages"].append("keywords not mapped to allowed actions")
        return result

    req = find_pending_request(cfg, pending_id) if pending_id else find_pending_request(cfg)

    for action in actions:
        if action == "deny_pending":
            ok, msg = deny_pending(cfg, pending_id or (req or {}).get("id"))
            result["messages"].append(f"deny_pending: {msg}" if ok else f"deny failed: {msg}")
            result["ok"] = result["ok"] or ok
            continue

        if action == "grant_session":
            path = write_email_grant(cfg)
            result["messages"].append(f"grant_session: {path.name} ({email_grant_minutes(cfg)} min)")
            result["ok"] = True
            og.audit_log(cfg, action="email_grant", target=str(path), ok=True, detail="email-reply")

        if action == "approve_pending":
            ok, msg = approve_pending(cfg, pending_id or (req or {}).get("id"))
            result["messages"].append(f"approve_pending: {msg}" if ok else f"approve skipped: {msg}")
            result["ok"] = result["ok"] or ok
            req = find_pending_request(cfg, msg, status="approved") if ok else req

        if action == "render_deploy_nudge":
            approved_req = req
            if approved_req and approved_req.get("status") != "approved":
                ok, pid = approve_pending(cfg, approved_req.get("id"))
                if ok:
                    approved_req = find_pending_request(cfg, pid, status="approved")
            marker = apply_render_deploy_nudge(cfg, approved_req)
            result["messages"].append(f"render_deploy_nudge: {marker.name}")
            result["ok"] = True

        if action == "git_push_reminder":
            marker = apply_git_push_reminder(cfg, req)
            result["messages"].append(f"git_push_reminder: {marker.name}")
            result["ok"] = True

        if action == "stop_team":
            path = apply_stop_team(cfg)
            result["messages"].append(f"stop_team: {path.name}")
            result["ok"] = True
            og.audit_log(cfg, action="email_stop", target=str(path), ok=True, detail="STOP keyword")

        if action == "cancel_orders":
            ok, msg = apply_cancel_orders(cfg)
            result["messages"].append(f"cancel_orders: {msg}")
            result["ok"] = result["ok"] or ok

        if action == "start_team":
            marker = _write_marker(
                cfg,
                "START-TEAM-APPROVED.txt",
                [f"approved_at={_now_iso()}", "action=start_team"],
            )
            result["messages"].append(f"start_team: {marker.name}")
            result["ok"] = True

    return result
