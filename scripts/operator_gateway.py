#!/usr/bin/env python3
"""User-granted operator gateway — time-limited tiers, whitelist actions, audit log.

v1 automation: launch .bat, open folders, start whitelisted programs, open URLs.
v2 (tier ``automate``, OFF by default): optional pywinauto/pyautogui — not implemented here.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "operator_protocol.yaml"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from security_utils import redact_line  # noqa: E402

FORBIDDEN_SHELL = ("|", ">", "<", "&", ";", "`", "$(")
VALID_TIERS = ("observe", "launch", "shell", "session", "automate")


def _config_path() -> Path:
    override = os.environ.get("OPERATOR_PROTOCOL_CONFIG")
    return Path(override) if override else CONFIG_PATH


def load_config() -> dict[str, Any]:
    with _config_path().open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def grant_path(cfg: dict[str, Any]) -> Path:
    override = os.environ.get("OPERATOR_GRANT_FILE")
    if override:
        return Path(override)
    return Path(cfg["paths"]["grant_file"])


def audit_path(cfg: dict[str, Any]) -> Path:
    override = os.environ.get("OPERATOR_AUDIT_LOG")
    if override:
        return Path(override)
    return Path(cfg["paths"]["audit_log"])


def _norm(p: str | Path) -> Path:
    return Path(os.path.normcase(os.path.normpath(str(p))))


def _under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def grant_duration_minutes(tier: str, cfg: dict[str, Any], *, source: str = "user-bat") -> int:
    arm_sources = ("arm-for-open", "auto-arm-marker", "arm-for-open-yes")
    if source in arm_sources and cfg.get("arm_grant_hours"):
        return int(float(cfg["arm_grant_hours"]) * 60)
    email_sources = ("email-reply", "email-command-listener")
    if source in email_sources:
        ec = cfg.get("email_command") or {}
        if ec.get("email_reply_grant_hours"):
            return int(float(ec["email_reply_grant_hours"]) * 60)
    return int(cfg["grant_minutes"].get(tier, 60))


def write_grant(
    tier: str,
    cfg: dict[str, Any],
    *,
    source: str = "user-bat",
    minutes: int | None = None,
) -> Path:
    duration = minutes if minutes is not None else grant_duration_minutes(tier, cfg, source=source)
    now = datetime.now(timezone.utc)
    payload = {
        "tier": tier,
        "granted_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=duration)).isoformat(),
        "granted_by": source,
    }
    path = grant_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def try_auto_grant_from_marker(cfg: dict[str, Any] | None = None) -> Path | None:
    """If Desktop marker + auto_grant_on_arm, write session grant (no prompt)."""
    cfg = cfg or load_config()
    if not cfg.get("auto_grant_on_arm"):
        return None
    marker = Path(cfg["paths"]["user_root"]) / "Desktop" / "OPERATOR-AUTO-ARM.txt"
    if not marker.is_file():
        return None
    return write_grant("session", cfg, source="auto-arm-marker")


def read_grant(cfg: dict[str, Any]) -> dict[str, Any] | None:
    path = grant_path(cfg)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def grant_status(grant: dict[str, Any] | None) -> tuple[bool, str]:
    if not grant:
        return False, "no grant file"
    tier = grant.get("tier")
    if tier not in VALID_TIERS:
        return False, "invalid tier in grant"
    exp_raw = grant.get("expires_at")
    if not exp_raw:
        return False, "grant missing expires_at"
    try:
        expires = datetime.fromisoformat(str(exp_raw).replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
    except ValueError:
        return False, "invalid expires_at"
    if datetime.now(timezone.utc) >= expires:
        return False, "grant expired"
    return True, "ok"


def tier_allows_action(tier: str, action: str, cfg: dict[str, Any]) -> bool:
    tiers = cfg.get("tiers") or {}
    spec = tiers.get(tier) or {}
    return action in (spec.get("actions") or [])


def _user_root(cfg: dict[str, Any]) -> Path:
    return _norm(cfg["paths"]["user_root"])


def _bridge_root(cfg: dict[str, Any]) -> Path:
    return _norm(cfg["paths"]["bridge_root"])


def _path_allowed(path: Path, entries: list[str], root: Path) -> bool:
    p = _norm(path)
    if not _under_root(p, root):
        return False
    for entry in entries:
        e = _norm(entry)
        if p == e:
            return True
        try:
            p.resolve().relative_to(e.resolve())
            return True
        except ValueError:
            continue
    return False


def _program_spec(cfg: dict[str, Any]) -> dict[str, Any]:
    return cfg.get("program_whitelist") or {}


def _resolve_program(target: str, cfg: dict[str, Any]) -> tuple[Path | None, list[str], str]:
    """Return (exe path, args, error)."""
    raw = target.strip()
    spec = _program_spec(cfg)
    aliases = spec.get("aliases") or {}
    paths = [_norm(p) for p in spec.get("paths") or []]

    if raw.lower() in {k.lower() for k in aliases}:
        key = next(k for k in aliases if k.lower() == raw.lower())
        entry = aliases[key]
        if isinstance(entry, str):
            exe = _norm(entry)
            return exe, [], ""
        if isinstance(entry, dict):
            exe_raw = entry.get("exe")
            if not exe_raw:
                return None, [], "alias missing exe"
            args = [str(a) for a in entry.get("args") or []]
            return _norm(exe_raw), args, ""

    p = _norm(raw)
    user_root = _user_root(cfg)
    if p.suffix.lower() != ".exe":
        return None, [], "program target must be .exe or alias"
    if not _under_root(p, user_root) and p not in paths:
        return None, [], "exe outside user_root and not on program paths list"
    if p not in paths:
        return None, [], "program exe not whitelisted"
    return p, [], ""


def _url_allowed(url: str, cfg: dict[str, Any]) -> bool:
    u = url.strip()
    if not u.lower().startswith("https://"):
        return False
    for prefix in cfg.get("url_whitelist") or []:
        if u.lower().startswith(str(prefix).lower()):
            return True
    health = cfg.get("health_urls") or []
    return u in health


def _service_allowed(name: str, cfg: dict[str, Any]) -> bool:
    services = cfg.get("service_whitelist") or []
    if not services:
        return False
    n = name.strip().lower()
    return any(str(s).strip().lower() == n for s in services)


def validate_target(action: str, target: str, cfg: dict[str, Any]) -> tuple[bool, str]:
    if action == "health":
        urls = cfg.get("health_urls") or []
        if target.strip() in urls:
            return True, "ok"
        return False, "health url not whitelisted"

    if action == "url":
        if _url_allowed(target, cfg):
            return True, "ok"
        return False, "url not whitelisted"

    if action == "program":
        exe, _args, err = _resolve_program(target, cfg)
        if err:
            return False, err
        if exe is None:
            return False, "program not resolved"
        return True, "ok"

    if action in ("service_start", "service_stop"):
        if not _service_allowed(target, cfg):
            return False, "service not whitelisted (list empty = deny all)"
        return True, "ok"

    if action in ("launch", "open"):
        p = _norm(target)
        user_root = _user_root(cfg)
        if not _under_root(p, user_root):
            return False, "path outside user_root"
        if action == "launch":
            ext = p.suffix.lower()
            if ext not in [e.lower() for e in cfg.get("launch_extensions") or []]:
                return False, "not a launchable extension"
            listed = [_norm(x) for x in cfg.get("launch_whitelist") or []]
            if p in listed:
                return True, "ok"
            bridge = _bridge_root(cfg)
            if _under_root(p, bridge) and ext in (".bat", ".exe", ".cmd"):
                return True, "ok"
            return False, "launch path not whitelisted"
        open_list = cfg.get("open_whitelist") or []
        if _path_allowed(p, open_list, user_root):
            return True, "ok"
        return False, "open path not whitelisted"

    if action == "shell":
        cmd = target.strip()
        for ch in FORBIDDEN_SHELL:
            if ch in cmd:
                return False, "shell metacharacters forbidden"
        lower = cmd.lower()
        for prefix in cfg.get("shell_whitelist") or []:
            if lower.startswith(prefix.lower()):
                return True, "ok"
        parts = cmd.split()
        if parts:
            script = _norm(parts[-1] if parts[0].lower().endswith("python.exe") else parts[0])
            for prefix in cfg.get("scripts_prefix_whitelist") or []:
                pre = _norm(prefix)
                if str(script).startswith(str(pre)) or script == pre:
                    return True, "ok"
        return False, "shell command not whitelisted"

    return False, f"unknown action {action}"


def audit_log(
    cfg: dict[str, Any],
    *,
    action: str,
    target: str,
    ok: bool,
    detail: str = "",
) -> None:
    path = audit_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_target = redact_line(target)[:500]
    safe_detail = redact_line(detail)[:200] if detail else ""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    status = "ok" if ok else "fail"
    line = f"{ts}\t{action}\t{safe_target}\t{status}"
    if safe_detail:
        line += f"\t{safe_detail}"
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_health(target: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(target, timeout=15) as resp:
            body = resp.read(4096).decode("utf-8", errors="replace")
        return True, body[:400]
    except Exception as exc:
        return False, type(exc).__name__


def run_launch(target: str) -> tuple[bool, str]:
    p = Path(target)
    if not p.is_file():
        return False, "file not found"
    subprocess.Popen(
        [str(p)],
        cwd=str(p.parent),
        shell=True,
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )
    return True, "started"


def run_open(target: str) -> tuple[bool, str]:
    p = Path(target)
    if not p.exists():
        return False, "path not found"
    os.startfile(str(p))  # noqa: S606 — Windows explorer/default handler
    return True, "opened"


def run_program(target: str, cfg: dict[str, Any]) -> tuple[bool, str]:
    exe, args, err = _resolve_program(target, cfg)
    if err:
        return False, err
    if exe is None or not exe.is_file():
        return False, "program exe not found"
    try:
        subprocess.Popen(
            [str(exe), *args],
            cwd=str(exe.parent),
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
    except OSError:
        try:
            os.startfile(str(exe))  # noqa: S606
        except OSError as ose:
            return False, f"start failed: {ose}"
    return True, "program started"


def run_url(target: str) -> tuple[bool, str]:
    url = target.strip()
    try:
        os.startfile(url)  # noqa: S606
        return True, "url opened"
    except OSError:
        if webbrowser.open(url):
            return True, "url opened (webbrowser)"
        return False, "could not open url"


def run_service(action: str, target: str) -> tuple[bool, str]:
    svc = target.strip()
    ps_action = "Start-Service" if action == "service_start" else "Stop-Service"
    script = f"{ps_action} -Name '{svc}' -ErrorAction Stop"
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, "service command timeout"
    out = redact_line(((proc.stdout or "") + (proc.stderr or "")).replace("\n", " | "))[:200]
    if proc.returncode != 0:
        return False, f"exit {proc.returncode}: {out}"
    return True, out or "ok"


def run_shell(target: str, cfg: dict[str, Any]) -> tuple[bool, str]:
    bridge = str(cfg["paths"]["bridge_root"])
    proc = subprocess.run(
        target,
        shell=True,
        cwd=bridge,
        capture_output=True,
        text=True,
        timeout=600,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    safe = redact_line(out.replace("\n", " | "))[:400]
    if proc.returncode != 0:
        return False, f"exit {proc.returncode}: {safe}"
    return True, safe or "exit 0"


def execute_action(action: str, target: str, cfg: dict[str, Any]) -> tuple[bool, str]:
    if action == "health":
        return run_health(target)
    if action == "launch":
        return run_launch(target)
    if action == "open":
        return run_open(target)
    if action == "shell":
        return run_shell(target, cfg)
    if action == "program":
        return run_program(target, cfg)
    if action == "url":
        return run_url(target)
    if action in ("service_start", "service_stop"):
        return run_service(action, target)
    return False, "unsupported action"


def run_action(action: str, target: str, cfg: dict[str, Any] | None = None) -> int:
    cfg = cfg or load_config()
    grant = read_grant(cfg)
    ok_grant, grant_reason = grant_status(grant)
    tier = (grant or {}).get("tier", "")

    def finish(success: bool, detail: str) -> int:
        audit_log(cfg, action=action, target=target, ok=success, detail=detail)
        print(detail)
        return 0 if success else 1

    if not ok_grant:
        return finish(False, f"denied: {grant_reason}")

    if not tier_allows_action(str(tier), action, cfg):
        return finish(False, f"denied: tier {tier} cannot {action}")

    valid, reason = validate_target(action, target, cfg)
    if not valid:
        return finish(False, f"denied: {reason}")

    success, detail = execute_action(action, target, cfg)
    return finish(success, detail)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Operator gateway (user-granted actions)")
    parser.add_argument(
        "--grant-tier",
        choices=["observe", "launch", "shell", "session"],
        help="Write time-limited grant (user bat only; not automate)",
    )
    parser.add_argument(
        "--action",
        choices=[
            "launch",
            "open",
            "shell",
            "health",
            "program",
            "url",
            "service_start",
            "service_stop",
        ],
        help="Perform whitelisted action",
    )
    parser.add_argument(
        "--target",
        help="Path, alias (cursor), command, health/url, or service name",
    )
    args = parser.parse_args(argv)

    cfg = load_config()

    if args.grant_tier:
        path = write_grant(args.grant_tier, cfg)
        print(f"grant written: {path} tier={args.grant_tier}")
        return 0

    if not args.action:
        parser.error("specify --action or --grant-tier")
    if not args.target:
        parser.error("--target required with --action")

    return run_action(args.action, args.target, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
