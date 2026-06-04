"""One-shot: create pending + send NEED APPROVAL for Deploy v5.5.9 (DO-NOW-LAUNCH.bat)."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import email_approval as erg
import team_email as te

def main() -> int:
    cfg = erg.load_config()
    entry = erg.create_pending_request(
        cfg,
        kind="deploy_render",
        title="Deploy v5.5.9 to Render",
        detail="Git push spy-options-bridge then Render Manual Deploy. Target /health version 5.5.9.",
        expires_hours=24,
        extra={"target_version": "5.5.9"},
    )
    pid = entry["id"]
    detail = (
        "Deploy v5.5.9 to Render after git push. "
        "Render: spy-options-bridge Manual Deploy. "
        "Verify https://spy-options-bridge.onrender.com/health shows 5.5.9."
    )
    ok = te.send_approval_needed(pid, "Deploy v5.5.9 to Render", detail)
    print("sent=", ok, "pending_id=", pid)
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
