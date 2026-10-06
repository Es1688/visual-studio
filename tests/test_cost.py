import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
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


def asset_entry(cost: float, created: str) -> dict:
    return {
        "id": f"demo-img-{cost:.3f}".replace(".", "-"),
        "path": f"projects/demo/generated/asset-{cost}.png",
        "type": "image",
        "provider": "fal",
        "model": "flux-dev",
        "api_version": None,
        "operation": "text_to_image",
        "prompt": "test",
        "seed": None,
        "params": {},
        "parents": [],
        "cost_usd": cost,
        "created_at": created,
        "qc": {"status": "pending", "checks": []},
    }


def seed_manifest(root: Path, entries: list[dict]) -> None:
    with (root / "projects" / "demo" / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump({"project": "demo", "entries": entries}, f)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def test_estimate_uses_capabilities(staged):
    models = {m["id"]: m for m in sc.load_capabilities()["models"]}
    width, height = sc.aspect_pixels("square")
    expected = round(models["flux-dev"]["price"] * (width * height / 1e6) * 2, 4)
    assert sc.estimate_cost("flux-dev", 2, aspect="square") == expected
    assert sc.estimate_cost("flux-pro-kontext", 2, aspect="square") == pytest.approx(0.08)


def test_unknown_model_exits(staged):
    with pytest.raises(SystemExit):
        sc.find_model("no-such-model")


def test_budget_check_blocks_day_limit(staged):
    seed_manifest(staged, [asset_entry(4.90, now_iso())])
    ok, problems = sc.budget_check("demo", 0.20)
    assert not ok
    assert any("day limit" in line for line in problems)


def test_budget_check_allows_within_limits(staged):
    seed_manifest(staged, [asset_entry(1.00, now_iso())])
    ok, problems = sc.budget_check("demo", 0.50)
    assert ok, problems


def test_spend_today_ignores_old_entries(staged):
    old = (datetime.now(UTC) - timedelta(days=2)).isoformat(timespec="seconds")
    seed_manifest(staged, [asset_entry(1.0, old)])
    assert sc.spend_today("demo") == 0.0
    assert sc.spend_total("demo") == pytest.approx(1.0)
