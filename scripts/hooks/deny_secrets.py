#!/usr/bin/env python3
"""PreToolUse guard: deny tool calls that touch secret files.

Wired in .zcode/config.json (hooks.enabled: true). Reads the hook payload from
stdin, scans tool_input for references to .env / *.key / *.pem files and exits
2 (deny for PreToolUse) on a match, 0 otherwise. Fails open on malformed input
so a bug here can never wedge the whole session.

`.env.example` stays readable: only a bare `.env` (optionally with a prefix)
is denied, not `.env.example`.
"""

from __future__ import annotations

import json
import re
import sys

SECRET_PATTERNS = [
    re.compile(r"\.env\b(?!\.)"),  # .env, prod/.env — but not .env.example
    re.compile(r"\.(?:key|pem)\b"),
]


def payload_text(tool_input: object) -> str:
    if isinstance(tool_input, dict):
        return json.dumps(tool_input, ensure_ascii=False)
    return str(tool_input)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    tool_input = payload.get("tool_input")
    if tool_input is None:
        return 0
    text = payload_text(tool_input)
    for pattern in SECRET_PATTERNS:
        match = pattern.search(text)
        if match:
            print(
                f"denied: '{match.group(0)}' looks like a secret file;"
                " secrets live in env vars only (see AGENTS.md)",
                file=sys.stderr,
            )
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
