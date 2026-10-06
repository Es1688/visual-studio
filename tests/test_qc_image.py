"""qc_image.py: manifest validation, image checks, planned entries, --set-status."""

import json
import random
import shutil
from pathlib import Path

import pytest
import qc_image
import studio_common as sc
from PIL import Image

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture()
def staged(tmp_path, monkeypatch):
    """Temporary repo root: real config/ and schema/, empty demo project."""
    shutil.copytree(REPO / "config", tmp_path / "config")
    shutil.copytree(REPO / "schema", tmp_path / "schema")
    (tmp_path / "projects" / "demo" / "generated").mkdir(parents=True)
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    return tmp_path


def noise_png(rel_path: str, width: int, height: int, *, flat: bool = False) -> None:
    """Write an image into the staged repo; flat=True gives a monochrome fill."""
    if flat:
        img = Image.new("L", (width, height), 128)
    else:
        rng = random.Random(42)
        img = Image.frombytes("L", (width, height), rng.randbytes(width * height))
    path = sc.REPO_ROOT / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG")


def seed_entry(asset_id: str, rel_path: str, *, dry_run: bool = False,
               aspect: str | None = None) -> None:
    params: dict = {}
    if aspect is not None:
        params["aspect"] = aspect
    sc.append_entry(
        "demo",
        {
            "id": asset_id,
            "path": rel_path,
            "type": "image",
            "provider": "fal",
            "model": "flux-dev",
            "api_version": None,
            "operation": "text_to_image",
            "prompt": "test",
            "seed": None,
            "params": params,
            "parents": [],
            "cost_usd": 0.0,
            "qc": {"status": "pending", "checks": []},
            "dry_run": dry_run,
        },
    )


def test_valid_png_is_ok(staged, capsys):
    noise_png("projects/demo/generated/demo-img-0001.png", 512, 512)
    seed_entry("demo-img-0001", "projects/demo/generated/demo-img-0001.png", aspect="square")
    assert qc_image.main(["--project", "demo"]) == 0
    assert "demo-img-0001: OK" in capsys.readouterr().out


def test_monochrome_fill_fails(staged, capsys):
    noise_png("projects/demo/generated/demo-img-0001.png", 512, 512, flat=True)
    seed_entry("demo-img-0001", "projects/demo/generated/demo-img-0001.png")
    assert qc_image.main(["--project", "demo"]) == 1
    out = capsys.readouterr().out
    assert "demo-img-0001: FAIL" in out
    assert "monochrome" in out


def test_dry_run_without_file_is_planned(staged, capsys):
    seed_entry("demo-img-0001", "projects/demo/generated/demo-img-0001.png", dry_run=True)
    assert qc_image.main(["--project", "demo"]) == 0
    assert "demo-img-0001: planned (no file)" in capsys.readouterr().out


def test_real_entry_without_file_fails(staged, capsys):
    seed_entry("demo-img-0001", "projects/demo/generated/demo-img-0001.png")
    assert qc_image.main(["--project", "demo"]) == 1
    assert "file missing" in capsys.readouterr().out


def test_small_image_fails_min_side(staged, capsys):
    noise_png("projects/demo/generated/demo-img-0001.png", 100, 100)
    seed_entry("demo-img-0001", "projects/demo/generated/demo-img-0001.png")
    assert qc_image.main(["--project", "demo"]) == 1
    assert "min_side" in capsys.readouterr().out


def test_min_side_flag_overrides_default(staged, capsys):
    noise_png("projects/demo/generated/demo-img-0001.png", 100, 100)
    seed_entry("demo-img-0001", "projects/demo/generated/demo-img-0001.png")
    assert qc_image.main(["--project", "demo", "--min-side", "100"]) == 0
    assert "demo-img-0001: OK" in capsys.readouterr().out


def test_wrong_aspect_fails(staged, capsys):
    noise_png("projects/demo/generated/demo-img-0001.png", 512, 512)
    seed_entry("demo-img-0001", "projects/demo/generated/demo-img-0001.png",
               aspect="portrait_9_16")
    assert qc_image.main(["--project", "demo"]) == 1
    assert "aspect" in capsys.readouterr().out


def test_matching_aspect_within_tolerance_passes(staged, capsys):
    noise_png("projects/demo/generated/demo-img-0001.png", 1080, 1350)
    seed_entry("demo-img-0001", "projects/demo/generated/demo-img-0001.png",
               aspect="portrait_4_5")
    assert qc_image.main(["--project", "demo"]) == 0


def test_set_status_updates_manifest(staged, capsys):
    noise_png("projects/demo/generated/demo-img-0001.png", 512, 512)
    seed_entry("demo-img-0001", "projects/demo/generated/demo-img-0001.png")
    code = qc_image.main(
        ["--project", "demo", "--asset", "demo-img-0001", "--set-status", "pass",
         "--note", "visual check ok"]
    )
    assert code == 0
    manifest = sc.load_manifest("demo")
    qc = manifest["entries"][0]["qc"]
    assert qc["status"] == "pass"
    assert any(check.get("note") == "visual check ok" for check in qc["checks"])
    sc.validate_manifest(manifest)


def test_set_status_unknown_asset_exits_2(staged):
    seed_entry("demo-img-0001", "projects/demo/generated/demo-img-0001.png", dry_run=True)
    assert qc_image.main(
        ["--project", "demo", "--asset", "demo-img-9999", "--set-status", "fail"]
    ) == 2


def test_set_status_without_asset_exits_2(staged):
    assert qc_image.main(["--project", "demo", "--set-status", "pass"]) == 2


def test_manual_status_is_not_rechecked(staged, capsys):
    noise_png("projects/demo/generated/demo-img-0001.png", 512, 512, flat=True)
    seed_entry("demo-img-0001", "projects/demo/generated/demo-img-0001.png")
    assert qc_image.main(
        ["--project", "demo", "--asset", "demo-img-0001", "--set-status", "retry",
         "--note", "redo after visual check"]
    ) == 0
    capsys.readouterr()
    assert qc_image.main(["--project", "demo"]) == 0
    out = capsys.readouterr().out
    assert "demo-img-0001: skipped" in out
    assert "qc.status=retry" in out


def test_manifest_schema_problem_exits_1(staged, capsys):
    manifest_path = staged / "projects" / "demo" / "manifest.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump({"project": "demo", "entries": [{"id": "broken"}]}, f)
    assert qc_image.main(["--project", "demo"]) == 1
    assert "schema" in capsys.readouterr().err
