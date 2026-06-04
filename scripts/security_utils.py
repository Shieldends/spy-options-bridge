"""Redact secrets from logs and subprocess output — never print passwords/keys."""

from __future__ import annotations

import re
import subprocess
import sys

# Env-style KEY=value lines and common secret-bearing keys
_SECRET_KEY_RE = re.compile(
    r"(?i)(password|secret|token|api[_-]?key|webhook|authorization|bearer|smtp_pass)",
)
_ASSIGN_RE = re.compile(
    r"(?i)^\s*([A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|KEY|WEBHOOK)[A-Z0-9_]*)\s*=\s*(.+)$"
)


def redact_line(line: str) -> str:
    """Mask likely secrets in a single log line."""
    m = _ASSIGN_RE.match(line)
    if m:
        return f"{m.group(1)}=***REDACTED***"
    if _SECRET_KEY_RE.search(line) and "=" in line:
        key, _, _val = line.partition("=")
        return f"{key.strip()}=***REDACTED***"
    # JSON-ish "webhookSecret": "..."
    line = re.sub(
        r'(?i)("?(?:webhookSecret|smtp_password|password|api_key|secret)"?\s*[:=]\s*)["\']?[^"\'\s,}]+',
        r"\1***REDACTED***",
        line,
    )
    return line


def background_python_exe(root: Path) -> str:
    """pythonw.exe for child workers (no black console flash)."""
    pw = root / ".venv" / "Scripts" / "pythonw.exe"
    if pw.is_file():
        return str(pw)
    py = root / ".venv" / "Scripts" / "python.exe"
    return str(py if py.is_file() else root)


def hidden_subprocess_flags() -> int:
    """Windows: run PowerShell/task tools without flashing a blue console."""
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def run_hidden(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    """subprocess.run with CREATE_NO_WINDOW on Windows."""
    if sys.platform == "win32":
        kwargs = dict(kwargs)
        kwargs.setdefault("creationflags", hidden_subprocess_flags())
    return subprocess.run(cmd, **kwargs)  # type: ignore[arg-type,call-overload]


def redact_text(text: str, *, max_len: int = 500) -> str:
    """Redact multiline subprocess or log output."""
    lines = [redact_line(ln) for ln in text.splitlines()]
    out = " | ".join(lines) if len(lines) <= 3 else "\n".join(lines)
    if len(out) > max_len:
        out = out[: max_len - 3] + "..."
    return out
