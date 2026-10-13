"""project_isolation.py hook: stdin contract, marker semantics, fail-open."""

import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "scripts" / "hooks" / "project_isolation.py"


def run_hook(payload: dict | str, active: str | None, root: Path) -> subprocess.CompletedProcess:
    """Run the hook with STUDIO_REPO_ROOT=<root>; `active` writes/clears the marker."""
    projects = root / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    marker = projects / ".active-project"
    if active is None:
        marker.unlink(missing_ok=True)
    else:
        marker.write_text(f"{active}\n", encoding="utf-8")
    data = payload if isinstance(payload, str) else json.dumps(payload)
    env = {**os.environ, "STUDIO_REPO_ROOT": str(root)}
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=data,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=env,
    )


def test_denies_read_of_neighbor_project(tmp_path):
    result = run_hook(
        {"tool_name": "Read", "tool_input": {"file_path": "projects/beta/brief.md"}},
        active="alpha",
        root=tmp_path,
    )
    assert result.returncode == 2


def test_denies_bash_into_neighbor(tmp_path):
    result = run_hook(
        {"tool_name": "Bash", "tool_input": {"command": "cat projects/beta/brief.md"}},
        active="alpha",
        root=tmp_path,
    )
    assert result.returncode == 2


def test_allows_active_project_paths(tmp_path):
    payloads = [
        {"tool_name": "Read", "tool_input": {"file_path": "projects/alpha/brief.md"}},
        {"tool_name": "Bash", "tool_input": {"command": "ls projects/"}},
        {
            "tool_name": "Bash",
            "tool_input": {"command": "uv run python scripts/gen.py --project alpha"},
        },
        {
            "tool_name": "Write",
            "tool_input": {"file_path": "projects/alpha/working/draft.png", "content": "x"},
        },
        {"tool_name": "Write", "tool_input": {"file_path": "/tmp/scratch.png", "content": "x"}},
    ]
    for payload in payloads:
        assert run_hook(payload, active="alpha", root=tmp_path).returncode == 0


def test_denies_studio_write_while_active(tmp_path):
    for tool in ("Edit", "Write", "ApplyPatch"):
        result = run_hook(
            {"tool_name": tool, "tool_input": {"file_path": "scripts/cost.py"}},
            active="alpha",
            root=tmp_path,
        )
        assert result.returncode == 2, tool
    result = run_hook(
        {"tool_name": "Edit", "tool_input": {"file_path": "config/budget.yaml"}},
        active="alpha",
        root=tmp_path,
    )
    assert result.returncode == 2


def test_allows_studio_access_without_marker(tmp_path):
    payloads = [
        {"tool_name": "Read", "tool_input": {"file_path": "projects/beta/brief.md"}},
        {"tool_name": "Edit", "tool_input": {"file_path": "scripts/cost.py"}},
    ]
    for payload in payloads:
        assert run_hook(payload, active=None, root=tmp_path).returncode == 0


def test_marker_itself_is_not_a_project_ref(tmp_path):
    result = run_hook(
        {"tool_name": "Read", "tool_input": {"file_path": "projects/.active-project"}},
        active="alpha",
        root=tmp_path,
    )
    assert result.returncode == 0


def test_fails_open_on_malformed_input(tmp_path):
    assert run_hook("not json at all", active="alpha", root=tmp_path).returncode == 0
