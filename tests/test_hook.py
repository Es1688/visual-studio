import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "scripts" / "hooks" / "deny_secrets.py"


def run_hook(payload: dict | str) -> subprocess.CompletedProcess:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=data,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_denies_read_dotenv():
    result = run_hook({"tool_name": "Read", "tool_input": {"file_path": "/repo/.env"}})
    assert result.returncode == 2


def test_denies_bash_touching_dotenv():
    result = run_hook({"tool_name": "Bash", "tool_input": {"command": "cat .env | head"}})
    assert result.returncode == 2


def test_denies_key_and_pem_files():
    for name in ("server.key", "cert.pem"):
        result = run_hook({"tool_name": "Read", "tool_input": {"file_path": f"/repo/{name}"}})
        assert result.returncode == 2


def test_allows_env_example():
    result = run_hook(
        {"tool_name": "Read", "tool_input": {"file_path": "/repo/.env.example"}}
    )
    assert result.returncode == 0


def test_allows_normal_calls():
    payloads = [
        {"tool_name": "Read", "tool_input": {"file_path": "PLAN.md"}},
        {"tool_name": "Bash", "tool_input": {"command": "uv run pytest -q"}},
        {"tool_name": "Bash", "tool_input": {"command": "export FOO=bar"}},
        {"tool_name": "Edit", "tool_input": {"file_path": "scripts/cost.py"}},
    ]
    for payload in payloads:
        assert run_hook(payload).returncode == 0


def test_fails_open_on_malformed_input():
    assert run_hook("not json at all").returncode == 0
