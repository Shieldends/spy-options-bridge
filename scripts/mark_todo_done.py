#!/usr/bin/env python3
"""Mark one item done in Desktop USER-TODO-CHECKLIST.json."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS.parent))
sys.path.insert(0, str(SCRIPTS))

import todo_checklist as tc  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Mark pre-market todo item complete")
    parser.add_argument("--item", required=True, help="Checklist key, e.g. email_setup_done")
    args = parser.parse_args()
    try:
        tc.mark_done(args.item)
    except ValueError as exc:
        print(exc)
        return 1
    print(f"Marked done: {tc.LABELS.get(args.item, args.item)}")
    print(f"Checklist: C:\\Users\\Shiel\\Desktop\\USER-TODO-CHECKLIST.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
