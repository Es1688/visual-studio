"""batch.py (P6): preflight gates, concurrent rows, report and exit codes.

All fal.ai HTTP goes through httpx.MockTransport (same pattern as
tests/test_gen_real.py); no network, no real FAL_KEY.
"""

import io
import json
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path

import batch
import fal_adapter
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
    """Temporary repo root: real config/ and schema/, copy of projects/demo."""
    shutil.copytree(REPO / "config", tmp_path / "config")
    shutil.copytree(REPO / "schema", tmp_path / "schema")
    shutil.copytree(REPO / "projects" / "demo", tmp_path / "projects" / "demo")
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("FAL_KEY", "test-key")
    return tmp_path


def mock_fal(fail_marker: str | None = None):
    """Thread-safe MockTransport over the queue API; returns (transport, calls).

    Submit calls whose payload contains `fail_marker` always answer 500
    (exhausting retries -> FalError -> gen_image exit 4).
    """
    calls = {"submit": 0, "status": 0, "response": 0, "download": 0}
    lock = threading.Lock()

    def bump(kind: str) -> None:
        with lock:
            calls[kind] += 1

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            bump("submit")
            if fail_marker and fail_marker in request.content.decode():
                return httpx.Response(500, text="row marked to fail")
            return httpx.Response(
                200,
                json={
                    "request_id": "req-test-1",
                    "status_url": f"{QUEUE_PREFIX}/status",
                    "response_url": QUEUE_PREFIX,
                },
            )
        if request.url.path.endswith("/status"):
            bump("status")
            return httpx.Response(200, json={"status": "COMPLETED"})
        if request.url.path.endswith("/requests/req-test-1"):
            bump("response")
            return httpx.Response(
                200, json={"images": [{"url": "https://media.test/img-0.png"}]}
            )
        bump("download")
        return httpx.Response(200, content=TINY_PNG)

    return httpx.MockTransport(handler), calls


def make_client(transport: httpx.MockTransport) -> fal_adapter.FalClient:
    return fal_adapter.FalClient(transport=transport, backoff=0.0, poll_interval_s=0.0)


def http_calls(calls: dict) -> int:
    return sum(value for value in calls.values())


def line(prompt: str, model: str = "flux-dev", aspect: str = "square") -> str:
    return f'"{prompt}",{model},{aspect},1'


def write_csv(staged: Path, rows_text: str) -> Path:
    path = staged / "projects" / "demo" / "batch.csv"  # inputs stay in-project
    path.write_text("prompt,model,aspect,count\n" + rows_text, encoding="utf-8")
    return path


def run_batch(staged: Path, csv_path: Path, *extra: str, client=None) -> int:
    return batch.main(["--project", "demo", "--csv", str(csv_path), *extra], client=client)


def snapshot() -> tuple[set[str], int, float]:
    """Manifest state before a run: ids, entry count, spend (tests must not
    depend on how many entries the repo's projects/demo already has)."""
    entries = sc.load_manifest("demo")["entries"]
    return {e["id"] for e in entries}, len(entries), sc.spend_total("demo")


def load_report(staged: Path) -> dict:
    reports = sorted((staged / "logs").glob("batch-*.json"))
    assert len(reports) == 1
    return json.loads(reports[0].read_text(encoding="utf-8"))


def test_three_rows_generate_and_manifest_grows(staged):
    transport, calls = mock_fal()
    csv_path = write_csv(
        staged,
        "\n".join(
            [
                line("warm autumn morning, space for text"),
                line("minimal pastel studio shot"),
                line("cozy evening, candles and plaid"),
            ]
        )
        + "\n",
    )
    ids_before, count_before, spend_before = snapshot()
    rc = run_batch(staged, csv_path, "--concurrency", "2", client=make_client(transport))
    assert rc == 0

    manifest = sc.load_manifest("demo")
    sc.validate_manifest(manifest)
    new_entries = [e for e in manifest["entries"] if e["id"] not in ids_before]
    assert len(new_entries) == 3

    per_row = sc.estimate_cost("flux-dev", 1, aspect="square")
    assert sc.spend_total("demo") - spend_before == pytest.approx(3 * per_row)
    for entry in new_entries:
        assert (staged / entry["path"]).read_bytes() == TINY_PNG

    assert calls["download"] == 3
    report = load_report(staged)
    assert report["totals"]["ok"] == 3
    assert report["totals"]["failed"] == 0
    assert report["totals"]["skipped"] == 0
    assert report["totals"]["spent_usd"] == pytest.approx(3 * per_row)


def test_budget_preflight_blocks_before_any_http(staged, capsys):
    today = datetime.now(UTC).isoformat(timespec="seconds")
    spendy_entry = {
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
        "cost_usd": 4.98,  # + 3 x ~0.03 planned > per_day 5.00 (config/budget.yaml)
        "created_at": today,
        "qc": {"status": "pending", "checks": []},
    }
    with (staged / "projects" / "demo" / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump({"project": "demo", "entries": [spendy_entry]}, f)

    transport, calls = mock_fal()
    csv_path = write_csv(
        staged,
        "\n".join([line("one"), line("two"), line("three")]) + "\n",
    )
    rc = run_batch(staged, csv_path, client=make_client(transport))

    assert rc == 2
    assert http_calls(calls) == 0
    assert "BUDGET EXCEEDED" in capsys.readouterr().out
    assert len(sc.load_manifest("demo")["entries"]) == 1  # nothing appended

    report = load_report(staged)
    per_row = sc.estimate_cost("flux-dev", 1, aspect="square")
    assert [row["estimate_usd"] for row in report["rows"]] == [per_row] * 3
    assert report["totals"]["ok"] == 0
    assert report["budget_problems"]


def test_one_failing_row_does_not_kill_batch(staged):
    transport, calls = mock_fal(fail_marker="make it fail")
    csv_path = write_csv(
        staged,
        "\n".join(
            [
                line("good row one"),
                line("make it fail, broken submit"),
                line("good row two"),
            ]
        )
        + "\n",
    )
    _, count_before, _ = snapshot()
    rc = run_batch(staged, csv_path, "--concurrency", "2", client=make_client(transport))
    assert rc == 1

    manifest = sc.load_manifest("demo")
    sc.validate_manifest(manifest)
    assert len(manifest["entries"]) - count_before == 2

    report = load_report(staged)
    assert report["totals"]["ok"] == 2
    assert report["totals"]["failed"] == 1
    failed = [row for row in report["rows"] if row["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["rc"] == 4  # FalError after retries -> gen_image exit 4

    assert calls["download"] == 2
    # max_retries_per_asset=3 (config/budget.yaml): 2 good submits + 4 for the doomed row
    assert calls["submit"] == 6


def test_limit_runs_only_first_valid_row(staged):
    transport, calls = mock_fal()
    csv_path = write_csv(
        staged,
        "\n".join([line("one"), line("two"), line("three")]) + "\n",
    )
    _, count_before, _ = snapshot()
    rc = run_batch(staged, csv_path, "--limit", "1", client=make_client(transport))
    assert rc == 0  # rows beyond --limit are intentional, not failures

    manifest = sc.load_manifest("demo")
    new_entries = manifest["entries"][count_before:]
    assert len(new_entries) == 1
    assert new_entries[0]["id"].startswith("demo-img-")
    assert calls["download"] == 1

    report = load_report(staged)
    assert report["totals"]["ok"] == 1
    assert report["totals"]["skipped"] == 2
    assert all("limit" in row["reason"] for row in report["rows"] if not row["ok"])


def test_dry_run_without_fal_key(staged, monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    csv_path = write_csv(
        staged,
        "\n".join([line("one"), line("two"), line("three")]) + "\n",
    )
    _, count_before, spend_before = snapshot()
    rc = run_batch(staged, csv_path, "--dry-run")  # client=None: no key needed
    assert rc == 0

    manifest = sc.load_manifest("demo")
    sc.validate_manifest(manifest)
    new_entries = manifest["entries"][count_before:]
    assert len(new_entries) == 3
    assert all(entry.get("dry_run") for entry in new_entries)
    assert all(entry["cost_usd"] == 0.0 for entry in new_entries)
    assert all(entry["params"]["planned_cost_usd"] > 0 for entry in new_entries)
    assert sc.spend_total("demo") == pytest.approx(spend_before)

    report = load_report(staged)
    assert report["mode"] == "dry-run"
    assert report["totals"]["ok"] == 3


def test_unknown_model_row_is_skipped_rest_run(staged):
    transport, calls = mock_fal()
    csv_path = write_csv(
        staged,
        "\n".join(
            [
                line("good row one"),
                "# a comment line that must be ignored",
                "",
                '"broken row",no-such-model,square,1',
                line("good row two"),
            ]
        )
        + "\n",
    )
    _, count_before, _ = snapshot()
    rc = run_batch(staged, csv_path, client=make_client(transport))
    assert rc == 1  # an invalid row counts as a failed (skipped) line

    manifest = sc.load_manifest("demo")
    assert len(manifest["entries"]) - count_before == 2
    assert calls["download"] == 2

    report = load_report(staged)
    assert len(report["rows"]) == 3  # comment and blank lines are not rows
    assert report["totals"]["ok"] == 2
    assert report["totals"]["skipped"] == 1
    assert report["totals"]["skipped_invalid"] == 1
    skipped = [row for row in report["rows"] if row["status"] == "skipped"]
    assert "no-such-model" in skipped[0]["reason"]
