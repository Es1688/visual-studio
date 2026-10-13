#!/usr/bin/env python3
"""PreToolUse guard: per-project isolation for the active project.

Same stdin contract as deny_secrets.py: {"tool_name": ..., "tool_input": ...},
exit 2 denies the call, 0 allows; any parse problem fails open. The active
project comes from the marker projects/.active-project (managed by
scripts/project.py new|use|off). While a project is active:

- any tool call referencing projects/<other>/ is denied — reads included;
- file writes (Edit/Write/ApplyPatch) outside projects/<active>/ (except /tmp)
  are denied: studio work requires `python scripts/project.py off` first.

Accepted gaps: neighbor access via Bash without a projects/<name>/ path in
the command text (e.g. `grep -r x projects/`), and studio writes made through
Bash — the deterministic input guards in the CLI scripts cover those.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# STUDIO_REPO_ROOT exists for tests; the hook itself always runs in the repo.
REPO_ROOT = Path(
    os.environ.get("STUDIO_REPO_ROOT")
    or Path(__file__).resolve().parent.parent.parent
)
MARKER = REPO_ROOT / "projects" / ".active-project"
PROJECT_REF = re.compile(r"projects/([A-Za-z0-9][A-Za-z0-9._-]*)/")
WRITE_TOOLS = {"Edit", "Write", "ApplyPatch"}
PATH_KEYS = ("file_path", "path", "folder_path", "notebook_path", "target_file")
TMP_PREFIXES = ("/tmp", "/var/tmp", "/dev/shm")


def payload_text(tool_input: object) -> str:
    if isinstance(tool_input, dict):
        return json.dumps(tool_input, ensure_ascii=False)
    return str(tool_input)


def active_project() -> str | None:
    try:
        name = MARKER.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return name or None


def write_target(tool_input: object) -> tuple[Path, bool] | None:
    """(target path, is_explicit_tmp) from the first path-ish field, or None."""
    if not isinstance(tool_input, dict):
        return None
    for key in PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            path = Path(value)
            return (
                (path if path.is_absolute() else REPO_ROOT / path),
                path.is_absolute() and str(path).startswith(TMP_PREFIXES),
            )
    return None


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

    active = active_project()
    if active is None:
        return 0  # studio mode: no isolation

    for match in PROJECT_REF.finditer(payload_text(tool_input)):
        if match.group(1) != active:
            print(
                f"denied: isolation — active project is '{active}',"
                f" '{match.group(0)}' belongs to another project;"
                " switch with `python scripts/project.py use <name>`",
                file=sys.stderr,
            )
            return 2

    if payload.get("tool_name") in WRITE_TOOLS:
        target = write_target(tool_input)
        if target is None:
            return 0
        path, is_tmp = target
        try:
            path.resolve().relative_to((REPO_ROOT / "projects" / active).resolve())
        except ValueError:
            if not is_tmp:
                print(
                    f"denied: isolation — active project is '{active}', writes go"
                    " only to its directory; studio changes need"
                    " `python scripts/project.py off` first",
                    file=sys.stderr,
                )
                return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
