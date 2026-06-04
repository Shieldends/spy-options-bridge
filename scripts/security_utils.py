"""Redact secrets from logs and subprocess output — never print passwords/keys."""

from __future__ import annotations

import re

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


def redact_text(text: str, *, max_len: int = 500) -> str:
    """Redact multiline subprocess or log output."""
    lines = [redact_line(ln) for ln in text.splitlines()]
    out = " | ".join(lines) if len(lines) <= 3 else "\n".join(lines)
    if len(out) > max_len:
        out = out[: max_len - 3] + "..."
    return out
