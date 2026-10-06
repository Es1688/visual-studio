"""export.py: preset cover-crop, format conversion, manifest record, error exits."""

import random
import shutil
from pathlib import Path

import export
import pytest
import studio_common as sc
import yaml
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


def append_record(asset_id: str, rel_path: str, *, dry_run: bool = False) -> None:
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
            "prompt": "test source",
            "seed": None,
            "params": {"aspect": "square"},
            "parents": [],
            "cost_usd": 0.0,
            "qc": {"status": "pending", "checks": []},
            "dry_run": dry_run,
        },
    )


def seed_source_asset(size: tuple[int, int] = (1080, 1080)) -> str:
    """Create demo-img-0001 with a real square PNG; return its id."""
    asset_id = "demo-img-0001"
    rel_path = f"projects/demo/generated/{asset_id}.png"
    width, height = size
    rng = random.Random(7)
    img = Image.frombytes("L", (width, height), rng.randbytes(width * height))
    path = sc.REPO_ROOT / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG")
    append_record(asset_id, rel_path)
    return asset_id


def run_export(*extra: str) -> int:
    return export.main(["--project", "demo", "--asset", "demo-img-0001", *extra])


def test_cover_crop_matches_preset_exactly(staged):
    seed_source_asset()
    assert run_export("--preset", "portrait_4x5") == 0
    out = staged / "projects" / "demo" / "working" / "demo-img-0002-portrait_4x5.png"
    assert out.is_file()
    with Image.open(out) as img:
        assert img.size == (1080, 1350)


def test_landscape_preset_upscales_square(staged):
    seed_source_asset()
    assert run_export("--preset", "landscape_16x9") == 0
    out = staged / "projects" / "demo" / "working" / "demo-img-0002-landscape_16x9.png"
    with Image.open(out) as img:
        assert img.size == (1920, 1080)


def test_jpg_and_webp_conversion_are_rgb(staged):
    seed_source_asset()
    expected_format = {"jpg": "JPEG", "webp": "WEBP"}
    for index, fmt in enumerate(("jpg", "webp")):
        assert run_export("--preset", "square_1x1", "--format", fmt) == 0
        new_id = f"demo-img-{index + 2:04d}"
        out = staged / "projects" / "demo" / "working" / f"{new_id}-square_1x1.{fmt}"
        assert out.is_file()
        with Image.open(out) as img:
            assert img.format == expected_format[fmt]
            assert img.mode == "RGB"
            assert img.size == (1080, 1080)


def test_png_export_keeps_source_mode(staged):
    seed_source_asset()
    assert run_export("--preset", "square_1x1") == 0
    out = staged / "projects" / "demo" / "working" / "demo-img-0002-square_1x1.png"
    with Image.open(out) as img:
        assert img.format == "PNG"
        assert img.mode == "L"


def test_manifest_records_export_with_parents(staged):
    seed_source_asset()
    assert run_export("--preset", "square_1x1", "--quality", "90") == 0
    manifest = sc.load_manifest("demo")
    assert len(manifest["entries"]) == 2
    record = manifest["entries"][1]
    assert record["id"] == "demo-img-0002"
    assert record["operation"] == "export"
    assert record["parents"] == ["demo-img-0001"]
    assert record["cost_usd"] == 0.0
    assert record["params"] == {"preset": "square_1x1", "format": "png", "quality": 90}
    assert record["path"] == "projects/demo/working/demo-img-0002-square_1x1.png"
    sc.validate_manifest(manifest)


def test_unknown_asset_exits_2(staged):
    seed_source_asset()
    assert export.main(
        ["--project", "demo", "--asset", "demo-img-9999", "--preset", "square_1x1"]
    ) == 2


def test_empty_manifest_exits_2(staged):
    assert export.main(
        ["--project", "demo", "--asset", "demo-img-0001", "--preset", "square_1x1"]
    ) == 2


def test_missing_source_file_exits_2(staged):
    append_record("demo-img-0001", "projects/demo/generated/demo-img-0001.png", dry_run=True)
    assert run_export("--preset", "square_1x1") == 2


def test_preset_missing_from_config_exits_2(staged):
    seed_source_asset()
    caps_path = sc.config_dir() / "capabilities.yaml"
    capabilities = sc.load_yaml(caps_path)
    del capabilities["export_presets"]["portrait_4x5"]
    with caps_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(capabilities, f)
    assert run_export("--preset", "portrait_4x5") == 2
