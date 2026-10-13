"""Marketplace card pipeline: project-type profiles (ADR-0004), the 3:4
frame, and qc_image.py --marketplace (dual gate, --since-id scope, ratio and
banned-phrase checks). Tests are self-contained: the profile is written
inline, nothing depends on gitignored projects/."""

import json
import random
import shutil
from pathlib import Path

import compose
import edit_image
import export
import gen_image
import pytest
import qc_image
import studio_common as sc
import yaml
from PIL import Image

REPO = Path(__file__).resolve().parent.parent

PROFILE_YAML = """
type: marketplace
marketplaces:
  defaults:
    ratio: 3x4
    min_width: 900
    min_height: 1200
    max_file_mb: 10
    formats: [jpg, png]
    product_share_min: 0.70
  wb:
    min_width: 700
    min_height: 900
  yandex:
    min_width: 300
    min_height: 300
    product_share_min: 0.66
banned_phrases:
  - хит продаж
  - № 1
  - лучший
  - бестселлер
  - самая низкая цена
  - уникальный дизайн
  - высокое качество
"""


def write_profile(root: Path, text: str = PROFILE_YAML) -> None:
    (root / "projects" / "marketplace" / "profile.yaml").write_text(
        text, encoding="utf-8"
    )


@pytest.fixture()
def staged_mp(tmp_path, monkeypatch):
    """Tmp repo root: real config/ and schema/, marketplace project with an
    inline profile and an empty demo project."""
    shutil.copytree(REPO / "config", tmp_path / "config")
    shutil.copytree(REPO / "schema", tmp_path / "schema")
    (tmp_path / "projects" / "marketplace" / "working").mkdir(parents=True)
    (tmp_path / "projects" / "demo").mkdir(parents=True)
    write_profile(tmp_path)
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    seed_anchor()  # the --since-id anchor every scoped run starts from
    return tmp_path


def noise_png(rel_path: str, width: int, height: int, *, fmt: str = "PNG") -> None:
    rng = random.Random(42)
    img = Image.frombytes("L", (width, height), rng.randbytes(width * height))
    path = sc.REPO_ROOT / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format=fmt)


def seed_mp_entry(
    asset_id: str,
    rel_path: str,
    *,
    operation: str = "text_to_image",
    params: dict | None = None,
) -> None:
    sc.append_entry(
        "marketplace",
        {
            "id": asset_id,
            "path": rel_path,
            "type": "image",
            "provider": "fal",
            "model": "flux-dev",
            "api_version": None,
            "operation": operation,
            "prompt": "test",
            "seed": None,
            "params": params or {},
            "parents": [],
            "cost_usd": 0.0,
            "qc": {"status": "pending", "checks": []},
        },
    )


def seed_anchor() -> None:
    """An out-of-scope entry the --since-id scope starts from."""
    seed_mp_entry("marketplace-img-0000", "projects/marketplace/working/anchor.png")


def run_mp(staged: Path, marketplace: str = "ozon",
           since_id: str = "marketplace-img-0000") -> int:
    argv = ["--project", "marketplace", "--marketplace", marketplace]
    if since_id:
        argv += ["--since-id", since_id]
    return qc_image.main(argv)


# --- load_profile -----------------------------------------------------------


def test_load_profile_reads_yaml(staged_mp):
    profile = sc.load_profile("marketplace")
    assert profile["type"] == "marketplace"
    assert profile["marketplaces"]["defaults"]["ratio"] == "3x4"


def test_load_profile_none_without_file(staged_mp):
    assert sc.load_profile("demo") is None


# --- 3:4 frame everywhere -----------------------------------------------------


def test_preset_3x4_wired_through_all_dictionaries():
    assert sc.ASPECT_TO_PRESET["portrait_3_4"] == "portrait_3x4"
    assert sc.aspect_pixels("portrait_3_4") == (1080, 1440)
    assert compose.RATIO_PRESETS["3x4"] == "portrait_3x4"
    assert "portrait_3x4" in export.PRESET_CHOICES
    assert qc_image.ASPECT_RATIOS["3x4"] == pytest.approx(0.75)
    assert qc_image.ASPECT_RATIOS["portrait_3_4"] == pytest.approx(0.75)
    assert "portrait_3_4" in gen_image.ASPECTS
    assert "portrait_3_4" in edit_image.ASPECTS


def test_capabilities_declare_3x4():
    caps = sc.load_capabilities()
    by_id = {model["id"]: model for model in caps["models"]}
    assert "portrait_3_4" in by_id["flux-dev"]["aspects"]
    assert by_id["flux-pro-kontext"]["aspect_params"]["portrait_3_4"] == "3:4"
    preset = caps["export_presets"]["portrait_3x4"]
    assert (preset["width"], preset["height"]) == (1080, 1440)


def test_gen_image_dry_run_accepts_portrait_3_4(staged_mp):
    rc = gen_image.main(
        ["--project", "demo", "--prompt", "test", "--aspect", "portrait_3_4",
         "--dry-run"]
    )
    assert rc == 0
    entry = sc.load_manifest("demo")["entries"][0]
    assert entry["params"]["aspect"] == "portrait_3_4"
    planned = entry["params"]["planned_cost_usd"]
    assert planned == pytest.approx(sc.estimate_cost("flux-dev", 1, aspect="portrait_3_4"))


# --- marketplace QC: gate and scope -------------------------------------------


def test_gate_rejects_project_without_profile(staged_mp, capsys):
    seed_mp_entry("demo-img-0001", "projects/demo/generated/x.png")
    assert qc_image.main(
        ["--project", "demo", "--marketplace", "ozon", "--since-id", "demo-img-0001"]
    ) == 2
    assert "only available in projects/marketplace" in capsys.readouterr().err


def test_gate_rejects_wrong_project_name(staged_mp, capsys):
    # profile says marketplace, but the directory is not projects/marketplace
    (staged_mp / "projects" / "demo" / "profile.yaml").write_text(PROFILE_YAML)
    assert qc_image.main(
        ["--project", "demo", "--marketplace", "ozon", "--since-id", "x"]
    ) == 2
    assert "only available in projects/marketplace" in capsys.readouterr().err


def test_gate_rejects_profile_of_other_type(staged_mp, capsys):
    write_profile(staged_mp, "type: avatars\n")
    assert run_mp(staged_mp) == 2
    assert "type: marketplace" in capsys.readouterr().err


def test_since_id_is_required(staged_mp, capsys):
    assert qc_image.main(
        ["--project", "marketplace", "--marketplace", "ozon"]
    ) == 2
    assert "--since-id" in capsys.readouterr().err


def test_since_id_must_exist(staged_mp, capsys):
    assert run_mp(staged_mp, since_id="marketplace-img-9999") == 2
    assert "not found" in capsys.readouterr().err


def test_since_id_without_marketplace_rejected(staged_mp):
    assert qc_image.main(
        ["--project", "marketplace", "--since-id", "marketplace-img-0001"]
    ) == 2


def test_scope_skips_entries_before_since_id(staged_mp, capsys):
    # a broken legacy entry (no file) stays out of the scoped run
    seed_mp_entry("marketplace-img-0001", "projects/marketplace/working/missing.png")
    noise_png("projects/marketplace/working/s2.png", 1080, 1440)
    seed_mp_entry(
        "marketplace-img-0002", "projects/marketplace/working/s2.png", params={},
    )
    assert run_mp(staged_mp, since_id="marketplace-img-0001") == 0
    out = capsys.readouterr().out
    assert "marketplace-img-0001:" not in out
    assert "marketplace-img-0002: OK" in out
    assert "1 entr(ies) after" in out


# --- marketplace QC: profile checks -------------------------------------------


def test_clean_slide_passes_full_profile(staged_mp, capsys):
    noise_png("projects/marketplace/working/slide1.png", 1080, 1440)
    seed_mp_entry(
        "marketplace-img-0001", "projects/marketplace/working/slide1.png",
        params={"aspect": "portrait_3_4"},
    )
    assert run_mp(staged_mp) == 0
    assert "marketplace-img-0001: OK" in capsys.readouterr().out


def test_small_resolution_fails(staged_mp, capsys):
    noise_png("projects/marketplace/working/slide1.png", 800, 1067)
    seed_mp_entry(
        "marketplace-img-0001", "projects/marketplace/working/slide1.png",
    )
    assert run_mp(staged_mp) == 1
    assert "resolution" in capsys.readouterr().out


def test_wb_uses_own_resolution(staged_mp, capsys):
    noise_png("projects/marketplace/working/slide1.png", 700, 934)
    seed_mp_entry(
        "marketplace-img-0001", "projects/marketplace/working/slide1.png",
    )
    assert run_mp(staged_mp, marketplace="wb") == 0
    assert "marketplace-img-0001: OK" in capsys.readouterr().out


def test_wrong_format_fails(staged_mp, capsys):
    noise_png(
        "projects/marketplace/working/slide1.jpg", 1080, 1440, fmt="JPEG",
    )
    write_profile(staged_mp, PROFILE_YAML.replace("formats: [jpg, png]",
                                                  "formats: [png]"))
    seed_mp_entry(
        "marketplace-img-0001", "projects/marketplace/working/slide1.jpg",
    )
    assert run_mp(staged_mp) == 1
    assert "format" in capsys.readouterr().out


def test_jpeg_alias_matches_jpg(staged_mp, capsys):
    noise_png("projects/marketplace/working/slide1.jpg", 1080, 1440, fmt="JPEG")
    seed_mp_entry(
        "marketplace-img-0001", "projects/marketplace/working/slide1.jpg",
    )
    assert run_mp(staged_mp) == 0


def test_oversize_file_fails(staged_mp, capsys):
    noise_png("projects/marketplace/working/slide1.png", 1080, 1440)  # > 50 KB
    write_profile(staged_mp, PROFILE_YAML.replace("max_file_mb: 10",
                                                  "max_file_mb: 0.05"))
    seed_mp_entry(
        "marketplace-img-0001", "projects/marketplace/working/slide1.png",
    )
    assert run_mp(staged_mp) == 1
    assert "file_size" in capsys.readouterr().out


def test_compose_with_wrong_ratio_fails(staged_mp, capsys):
    noise_png("projects/marketplace/working/cmp.png", 1080, 1350)  # 4x5
    seed_mp_entry(
        "marketplace-img-0001", "projects/marketplace/working/cmp.png",
        operation="compose",
        params={"ratio": "4x5", "template": "x.html", "data": {"headline": "ok"}},
    )
    assert run_mp(staged_mp) == 1
    assert "marketplace ratio" in capsys.readouterr().out


def test_support_assets_are_not_ratio_checked(staged_mp, capsys):
    # remove_background cutout keeps the source geometry: square, still OK
    noise_png("projects/marketplace/working/cutout.png", 1200, 1200)
    seed_mp_entry(
        "marketplace-img-0001", "projects/marketplace/working/cutout.png",
        operation="remove_background",
    )
    # inpaint follows match_input models: never ratio-checked (4:3 frame)
    noise_png("projects/marketplace/working/inpaint.png", 1600, 1200)
    seed_mp_entry(
        "marketplace-img-0002", "projects/marketplace/working/inpaint.png",
        operation="inpaint", params={"aspect": "portrait_3_4"},
    )
    assert run_mp(staged_mp) == 0
    assert capsys.readouterr().out.count(": OK") == 2


def test_generation_with_slide_aspect_is_ratio_checked(staged_mp, capsys):
    noise_png("projects/marketplace/working/bg.png", 1080, 1350)  # drifted 4x5
    seed_mp_entry(
        "marketplace-img-0001", "projects/marketplace/working/bg.png",
        operation="text_to_image", params={"aspect": "portrait_3_4"},
    )
    assert run_mp(staged_mp) == 1
    assert "marketplace ratio" in capsys.readouterr().out


# --- marketplace QC: banned phrases --------------------------------------------


def compose_entry_with_text(staged: Path, text: str) -> None:
    noise_png("projects/marketplace/working/cmp.png", 1080, 1440)
    seed_mp_entry(
        "marketplace-img-0001", "projects/marketplace/working/cmp.png",
        operation="compose",
        params={"ratio": "3x4", "template": "mp-cover.html",
                "data": {"headline": text}},
    )


def test_banned_phrase_exact_case_fails(staged_mp, capsys):
    compose_entry_with_text(staged_mp, "Хит продаж зимы")
    assert run_mp(staged_mp) == 1
    out = capsys.readouterr().out
    assert "banned phrase 'хит продаж'" in out and "data.headline" in out


def test_banned_phrase_mixed_case_and_punctuation_fails(staged_mp, capsys):
    compose_entry_with_text(staged_mp, "ХИТ ПРОДАЖ!")
    assert run_mp(staged_mp) == 1
    assert "banned phrase" in capsys.readouterr().out


def test_banned_phrase_no_sign_without_space_fails(staged_mp, capsys):
    compose_entry_with_text(staged_mp, "№1 в категории")
    assert run_mp(staged_mp) == 1
    assert "banned phrase '№ 1'" in capsys.readouterr().out


def test_banned_phrase_best_word_fails(staged_mp, capsys):
    compose_entry_with_text(staged_mp, "Лучший выбор")
    assert run_mp(staged_mp) == 1
    assert "banned phrase 'лучший'" in capsys.readouterr().out


def test_clean_text_passes(staged_mp, capsys):
    compose_entry_with_text(staged_mp, "Греет бетон за 6 часов")
    assert run_mp(staged_mp) == 0
    assert "marketplace-img-0001: OK" in capsys.readouterr().out


def test_banned_phrases_checked_for_compose_only(staged_mp, capsys):
    noise_png("projects/marketplace/working/bg.png", 1080, 1440)
    seed_mp_entry(
        "marketplace-img-0001", "projects/marketplace/working/bg.png",
        operation="text_to_image", params={"aspect": "portrait_3_4"},
    )
    # prompt text is not slide copy; data only exists on compose entries
    assert run_mp(staged_mp) == 0


# --- offline render of a slide template at 3x4 ---------------------------------

try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as _pw:
        _exe = _pw.chromium.executable_path
        CHROMIUM_OK = bool(_exe) and Path(_exe).exists()
except Exception:
    CHROMIUM_OK = False

RENDER_REASON = "Chromium is not installed; run: uv run playwright install chromium"


@pytest.fixture()
def staged_compose(tmp_path, monkeypatch):
    shutil.copytree(REPO / "config", tmp_path / "config")
    shutil.copytree(REPO / "schema", tmp_path / "schema")
    (tmp_path / "projects" / "demo" / "working").mkdir(parents=True)
    monkeypatch.setattr(sc, "REPO_ROOT", tmp_path)
    return tmp_path


def seed_background(root: Path) -> str:
    bg = root / "projects" / "demo" / "working" / "demo-img-0001.png"
    Image.new("RGB", (1024, 1024), (120, 80, 60)).save(bg)
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
        "created_at": "2026-10-13T00:00:00+00:00",
        "qc": {"status": "pending", "checks": []},
    }
    (root / "projects" / "demo" / "manifest.json").write_text(
        json.dumps({"project": "demo", "entries": [entry]}),
        encoding="utf-8",
    )
    return "demo-img-0001"


@pytest.mark.xfail(not CHROMIUM_OK, reason=RENDER_REASON)
def test_compose_renders_marketplace_3x4(staged_compose):
    seed_background(staged_compose)
    template = staged_compose / "projects" / "demo" / "templates" / "slide.html"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text(
        '<h1 class="headline">{{headline}}</h1>'
        '<div class="logo-slot">{{logo}}</div>',
        encoding="utf-8",
    )
    data = staged_compose / "projects" / "demo" / "slide-data.json"
    data.write_text('{"headline": "Теплый пол"}', encoding="utf-8")

    rc = compose.main(
        ["--project", "demo", "--asset", "demo-img-0001",
         "--template", "projects/demo/templates/slide.html",
         "--data", "projects/demo/slide-data.json", "--ratio", "3x4"]
    )
    assert rc == 0
    out = staged_compose / "projects" / "demo" / "working" / "demo-img-0001-cmp-3x4.png"
    with Image.open(out) as img:
        assert img.size == (1080, 1440)
    entry = [
        e for e in sc.load_manifest("demo")["entries"] if e["operation"] == "compose"
    ][0]
    assert entry["params"]["ratio"] == "3x4"
    sc.validate_manifest(sc.load_manifest("demo"))


def test_profile_yaml_is_valid(tmp_path):
    parsed = yaml.safe_load(PROFILE_YAML)
    assert parsed["type"] == "marketplace"
    assert parsed["marketplaces"]["wb"]["min_width"] == 700
