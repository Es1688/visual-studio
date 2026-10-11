"""Tests for scripts/compose.py: data preparation, manifest wiring, error codes.

The render test uses headless Chromium (local, no network); it is xfail with a
hint when the browser is not installed on the host.
"""

import json
import shutil
from pathlib import Path

import compose
import pytest
import studio_common as sc
from PIL import Image

REPO = Path(__file__).resolve().parent.parent

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as _pw:
        _exe = _pw.chromium.executable_path
        CHROMIUM_OK = bool(_exe) and Path(_exe).exists()
except Exception:
    CHROMIUM_OK = False

RENDER_REASON = "Chromium is not installed; run: uv run playwright install chromium"


@pytest.fixture()
def staged(tmp_path, monkeypatch):
    """Temporary repo root: real config/, schema/ and templates/, demo project."""
    shutil.copytree(REPO / "config", tmp_path / "config")
    shutil.copytree(REPO / "schema", tmp_path / "schema")
    shutil.copytree(REPO / "templates", tmp_path / "templates")
    (tmp_path / "projects" / "demo" / "working").mkdir(parents=True)
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    return tmp_path


def seed_background(root: Path) -> str:
    """Pillow stub 1024x1024 in working/ + its manifest entry; returns asset id."""
    bg_path = root / "projects" / "demo" / "working" / "demo-img-0001.png"
    Image.new("RGB", (1024, 1024), (120, 80, 60)).save(bg_path)
    entry = {
        "id": "demo-img-0001",
        "path": "projects/demo/working/demo-img-0001.png",
        "type": "image",
        "provider": "fal",
        "model": "flux-dev",
        "api_version": None,
        "operation": "text_to_image",
        "prompt": "test background",
        "seed": None,
        "params": {},
        "parents": [],
        "cost_usd": 0.0,
        "created_at": "2026-10-06T00:00:00+00:00",
        "qc": {"status": "pending", "checks": []},
    }
    manifest = root / "projects" / "demo" / "manifest.json"
    manifest.write_text(
        json.dumps({"project": "demo", "entries": [entry]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return "demo-img-0001"


def write_data(root: Path, data: dict) -> str:
    path = root / "data.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return "data.json"


def run_compose(staged: Path, *, asset: str = "demo-img-0001", data_name: str = "data.json",
                template: str = "templates/banner.html", ratio: str = "4x5") -> int:
    return compose.main(
        [
            "--project", "demo",
            "--asset", asset,
            "--template", template,
            "--data", data_name,
            "--ratio", ratio,
        ]
    )


@pytest.mark.xfail(not CHROMIUM_OK, reason=RENDER_REASON)
def test_compose_renders_preset_size_and_records_manifest(staged):
    asset = seed_background(staged)
    logo = staged / "projects" / "demo" / "working" / "logo.png"
    Image.new("RGBA", (200, 80), (255, 255, 255, 255)).save(logo)
    data = {
        "headline": "Осенняя распродажа −20%",
        "sub": "Керамическая кружка",
        "cta": "Подробнее",
        "logo": "projects/demo/working/logo.png",
    }
    write_data(staged, data)

    assert run_compose(staged) == 0

    out = staged / "projects" / "demo" / "working" / f"{asset}-cmp-4x5.png"
    assert out.is_file()
    preset = sc.load_capabilities()["export_presets"]["portrait_4x5"]
    with Image.open(out) as img:
        assert img.format == "PNG"
        assert img.size == (preset["width"], preset["height"])

    manifest = sc.load_manifest("demo")
    sc.validate_manifest(manifest)  # schema + unique ids
    entries = [e for e in manifest["entries"] if e["operation"] == "compose"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["parents"] == ["demo-img-0001"]
    assert entry["cost_usd"] == 0.0
    assert entry["path"] == "projects/demo/working/demo-img-0001-cmp-4x5.png"
    assert entry["params"]["template"] == "templates/banner.html"
    assert entry["params"]["ratio"] == "4x5"
    assert entry["params"]["data"] == data


def test_compose_unknown_asset_exits_2(staged):
    write_data(staged, {"headline": "x"})
    assert run_compose(staged, asset="demo-img-9999") == 2


def test_compose_missing_template_exits_2(staged):
    seed_background(staged)
    write_data(staged, {"headline": "x"})
    assert run_compose(staged, template="templates/nope.html") == 2


def test_compose_missing_data_exits_2(staged):
    seed_background(staged)
    assert run_compose(staged, data_name="nope.json") == 2


def test_compose_unknown_ratio_exits_2(staged):
    seed_background(staged)
    write_data(staged, {"headline": "x"})
    assert run_compose(staged, ratio="21x9") == 2


def test_compose_missing_logo_exits_2(staged):
    seed_background(staged)
    write_data(staged, {"headline": "x", "logo": "projects/demo/working/nope.png"})
    assert run_compose(staged) == 2


def test_compose_no_args_keeps_manifest(staged):
    seed_background(staged)
    with pytest.raises(SystemExit) as exc:
        compose.main([])
    assert exc.value.code == 2
    assert len(sc.load_manifest("demo")["entries"]) == 1


def test_fill_template_extra_placeholders_from_data(staged):
    bg = staged / "projects" / "demo" / "working" / "demo-img-0001.png"
    logo = staged / "projects" / "demo" / "working" / "logo.png"
    Image.new("RGBA", (40, 40)).save(logo)
    template = (
        '<h1>{{headline}}</h1><b>{{spec1_value}}</b> {{spec1_label}}'
        '<img src="{{logo}}"><i>{{background}}</i>'
    )
    data = {
        "headline": "кабель <74 м>",
        "spec1_value": "2900 Вт",
        "spec1_label": "мощность & ток",
        "logo": "projects/demo/working/logo.png",
    }
    filled = compose.fill_template(template, bg, data)
    assert "кабель &lt;74 м&gt;" in filled  # plain-text values are escaped
    assert "2900 Вт" in filled and "мощность &amp; ток" in filled
    assert 'class="logo"' in filled  # logo/background keep typed handling
    assert bg.resolve().as_uri() in filled
    assert "{{" not in filled


def test_fill_template_missing_placeholder_raises(staged):
    bg = staged / "projects" / "demo" / "working" / "demo-img-0001.png"
    with pytest.raises(compose.ComposeError, match="spec9_value"):
        compose.fill_template("<p>{{spec9_value}}</p>", bg, {"spec1_value": "74"})
