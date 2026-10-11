"""Real-mode tests for gen_image + fal_adapter. All HTTP is mocked via
httpx.MockTransport — no network, no real FAL_KEY (AGENTS.md, «Секреты»)."""

import io
import json
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path

import fal_adapter
import gen_image
import httpx
import pytest
import studio_common as sc
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
QUEUE_PREFIX = "https://queue.fal.run/fal-ai/flux/dev/requests/req-test-1"


def tiny_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (180, 60, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


TINY_PNG = tiny_png()


@pytest.fixture()
def staged(tmp_path, monkeypatch):
    """Temporary repo root: real config/ and schema/, empty demo project."""
    shutil.copytree(REPO / "config", tmp_path / "config")
    shutil.copytree(REPO / "schema", tmp_path / "schema")
    (tmp_path / "projects" / "demo" / "references").mkdir(parents=True)
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("FAL_KEY", "test-key")
    return tmp_path


def mock_fal(fail_submits: int = 0):
    """MockTransport speaking the queue API; returns (transport, call counters)."""
    calls = {"submit": 0, "status": 0, "response": 0, "download": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            calls["submit"] += 1
            calls["auth"] = request.headers.get("Authorization")
            if calls["submit"] <= fail_submits:
                return httpx.Response(500, text="transient boom")
            return httpx.Response(
                200,
                json={
                    "request_id": "req-test-1",
                    "status_url": f"{QUEUE_PREFIX}/status",
                    "response_url": QUEUE_PREFIX,
                },
            )
        if request.url.path.endswith("/status"):
            calls["status"] += 1
            return httpx.Response(200, json={"status": "COMPLETED"})
        if request.url.path.endswith("/requests/req-test-1"):
            calls["response"] += 1
            return httpx.Response(
                200, json={"images": [{"url": "https://media.test/img-0.png"}]}
            )
        calls["download"] += 1
        return httpx.Response(200, content=TINY_PNG)

    return httpx.MockTransport(handler), calls


def make_client(transport: httpx.MockTransport) -> fal_adapter.FalClient:
    return fal_adapter.FalClient(transport=transport, backoff=0.0, poll_interval_s=0.0)


def http_calls(calls: dict) -> int:
    return sum(value for key, value in calls.items() if isinstance(value, int))


def run_real(client: fal_adapter.FalClient | None) -> int:
    return gen_image.main(
        ["--project", "demo", "--prompt", "warm autumn morning", "--seed", "42"],
        client=client,
    )


def test_real_run_writes_file_and_manifest(staged):
    transport, calls = mock_fal()
    assert run_real(make_client(transport)) == 0

    png = staged / "projects" / "demo" / "generated" / "demo-img-0001.png"
    assert png.is_file()
    assert png.read_bytes() == TINY_PNG

    manifest = sc.load_manifest("demo")
    sc.validate_manifest(manifest)
    entry = manifest["entries"][0]
    assert "dry_run" not in entry
    expected_cost = sc.estimate_cost("flux-dev", 1, aspect="square")
    assert entry["cost_usd"] == pytest.approx(expected_cost)
    assert entry["params"]["endpoint"] == "fal-ai/flux/dev"
    assert entry["seed"] == 42
    assert sc.spend_total("demo") == pytest.approx(expected_cost)

    assert http_calls(calls) == 4  # submit + status + response + download
    assert calls["download"] == 1
    assert calls["auth"] == "Key test-key"


def test_missing_fal_key_exits_2(staged, monkeypatch, capsys):
    monkeypatch.delenv("FAL_KEY", raising=False)
    transport, calls = mock_fal()
    rc = gen_image.main(["--project", "demo", "--prompt", "x"])
    assert rc == 2
    assert "FAL_KEY is not set" in capsys.readouterr().err
    assert http_calls(calls) == 0


def test_day_budget_exhausted_blocks_before_http(staged):
    today = datetime.now(UTC).isoformat(timespec="seconds")
    spend = 4.98  # + 0.025 planned > per_day 5.00 (config/budget.yaml)
    entry = {
        "id": "demo-img-0001",
        "path": "projects/demo/generated/demo-img-0001.png",
        "type": "image",
        "provider": "fal",
        "model": "flux-dev",
        "api_version": None,
        "operation": "text_to_image",
        "prompt": "previous run",
        "seed": None,
        "params": {},
        "parents": [],
        "cost_usd": spend,
        "created_at": today,
        "qc": {"status": "pending", "checks": []},
    }
    with (staged / "projects" / "demo" / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump({"project": "demo", "entries": [entry]}, f)

    transport, calls = mock_fal()
    assert run_real(make_client(transport)) == 2
    assert http_calls(calls) == 0


def test_submit_retry_on_500_then_success(staged):
    transport, calls = mock_fal(fail_submits=1)
    assert run_real(make_client(transport)) == 0
    assert calls["submit"] == 2
    assert (staged / "projects" / "demo" / "generated" / "demo-img-0001.png").is_file()


def test_generate_accepts_single_image_response(staged):
    """birefnet-style endpoints return {"image": {...}} instead of images[]."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "request_id": "req-1",
                    "status_url": "https://queue.fal.run/m/requests/req-1/status",
                    "response_url": "https://queue.fal.run/m/requests/req-1",
                },
            )
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"status": "COMPLETED"})
        if "/requests/" in request.url.path:
            return httpx.Response(200, json={"image": {"url": "https://media.test/single.png"}})
        return httpx.Response(200, content=TINY_PNG)

    client = make_client(httpx.MockTransport(handler))
    assert client.generate("fal-ai/birefnet/v2", {}) == [TINY_PNG]


def test_model_without_text_to_image_exits_2(staged, capsys):
    rc = gen_image.main(["--project", "demo", "--prompt", "x", "--model", "esrgan"])
    assert rc == 2
    assert "does not support text_to_image" in capsys.readouterr().err


def test_append_entry_is_thread_safe(staged):
    """Concurrent append_entry calls merge into one valid manifest (P6 batch)."""

    def entry(index: int) -> dict:
        return {
            "id": f"demo-img-{index:04d}",
            "path": f"projects/demo/generated/demo-img-{index:04d}.png",
            "type": "image",
            "provider": "fal",
            "model": "flux-dev",
            "api_version": None,
            "operation": "text_to_image",
            "prompt": "threaded",
            "seed": None,
            "params": {},
            "parents": [],
            "cost_usd": 0.0,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "qc": {"status": "pending", "checks": []},
        }

    errors: list[Exception] = []

    def worker(index: int) -> None:
        try:
            sc.append_entry("demo", entry(index))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(1, 9)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    manifest = sc.load_manifest("demo")
    sc.validate_manifest(manifest)
    assert len(manifest["entries"]) == 8
