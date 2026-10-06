import shutil
from pathlib import Path

import gen_image
import pytest
import qc_image
import studio_common as sc

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture()
def staged(tmp_path, monkeypatch):
    """Temporary repo root: real config/ and schema/, empty demo project."""
    shutil.copytree(REPO / "config", tmp_path / "config")
    shutil.copytree(REPO / "schema", tmp_path / "schema")
    (tmp_path / "projects" / "demo" / "references").mkdir(parents=True)
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    return tmp_path


def run_dry_run() -> int:
    return gen_image.main(
        ["--project", "demo", "--prompt", "warm autumn morning, wooden table", "--dry-run"]
    )


def test_dry_run_appends_valid_entry(staged):
    assert run_dry_run() == 0
    manifest = sc.load_manifest("demo")
    assert len(manifest["entries"]) == 1
    sc.validate_manifest(manifest)
    entry = manifest["entries"][0]
    assert entry["dry_run"] is True
    assert entry["cost_usd"] == 0.0
    assert entry["params"]["planned_cost_usd"] > 0


def test_ids_are_sequential(staged):
    assert run_dry_run() == 0
    assert run_dry_run() == 0
    ids = [e["id"] for e in sc.load_manifest("demo")["entries"]]
    assert ids == ["demo-img-0001", "demo-img-0002"]


def test_dry_run_does_not_count_as_spend(staged):
    run_dry_run()
    assert sc.spend_total("demo") == 0.0


def test_qc_image_passes_after_dry_run(staged):
    run_dry_run()
    assert qc_image.main(["--project", "demo"]) == 0
