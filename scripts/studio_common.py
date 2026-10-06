"""Shared helpers for visual-studio scripts: configs, manifest, budget, asset ids.

Every script writes manifest entries in one format (schema/manifest.schema.json);
stage 2 (video) will reuse it with type="video".
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

# Serializes manifest writes across threads (P6 batch runs); append_entry
# re-reads the manifest inside the lock so concurrent writers merge entries.
_MANIFEST_LOCK = threading.Lock()


def config_dir() -> Path:
    return REPO_ROOT / "config"


def schema_path() -> Path:
    return REPO_ROOT / "schema" / "manifest.schema.json"


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_capabilities() -> dict:
    return load_yaml(config_dir() / "capabilities.yaml")


def load_budget() -> dict:
    return load_yaml(config_dir() / "budget.yaml")


def find_model(model_id: str) -> dict:
    models = load_capabilities().get("models", [])
    for model in models:
        if model["id"] == model_id:
            return model
    known = ", ".join(model["id"] for model in models)
    raise SystemExit(f"Unknown model '{model_id}'. Known models: {known}")


# Studio aspect names (CLI) -> delivery presets in capabilities.yaml.
ASPECT_TO_PRESET = {
    "square": "square_1x1",
    "landscape_16_9": "landscape_16x9",
    "portrait_9_16": "portrait_9x16",
    "portrait_4_5": "portrait_4x5",
}


def aspect_pixels(aspect: str) -> tuple[int, int]:
    """Pixel size of a studio aspect (config/capabilities.yaml, export_presets)."""
    preset = load_capabilities()["export_presets"][ASPECT_TO_PRESET[aspect]]
    return int(preset["width"]), int(preset["height"])


def estimate_cost(
    model_id: str,
    count: int = 1,
    aspect: str | None = None,
    megapixels: float | None = None,
) -> float:
    """Cost estimate honouring the provider's price unit (capabilities.yaml).

    per_image: flat price x count. per_megapixel: price x MP of the requested
    size x count (MP from the aspect preset, or explicitly — e.g. upscale
    passes output MP = input MP x scale^2). free: 0.
    """
    model = find_model(model_id)
    unit = model.get("price_unit", "per_image")
    if unit == "free":
        return 0.0
    if unit == "per_image":
        return round(model["price"] * count, 4)
    if megapixels is None:
        width, height = aspect_pixels(aspect or "square")
        megapixels = width * height / 1e6
    return round(model["price"] * megapixels * count, 4)


def confirm_threshold() -> float:
    return float(load_budget().get("confirm_threshold", 0.0))


def max_retries() -> int:
    return int(load_budget().get("max_retries_per_asset", 3))


def project_dir(project: str) -> Path:
    path = REPO_ROOT / "projects" / project
    if not path.is_dir():
        raise SystemExit(f"Unknown project '{project}': {path} not found")
    return path


def manifest_path(project: str) -> Path:
    return project_dir(project) / "manifest.json"


def load_manifest(project: str) -> dict:
    path = manifest_path(project)
    if not path.exists():
        return {"project": project, "entries": []}
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def validate_manifest(manifest: dict) -> None:
    with schema_path().open(encoding="utf-8") as f:
        schema = json.load(f)
    jsonschema.validate(manifest, schema)
    ids = [entry["id"] for entry in manifest.get("entries", [])]
    duplicates = sorted({asset_id for asset_id in ids if ids.count(asset_id) > 1})
    if duplicates:
        raise jsonschema.ValidationError(f"duplicate asset ids: {duplicates}")


def next_asset_id(project: str, prefix: str = "img") -> str:
    entries = load_manifest(project).get("entries", [])
    return f"{project}-{prefix}-{len(entries) + 1:04d}"


def append_entry(project: str, entry: dict) -> dict:
    """Validate and append one asset record; creates project subdirs as needed.

    Thread-safe: a module-level lock plus a manifest re-read inside the lock,
    so concurrent writers (batch, P6) never drop each other's entries.
    """
    with _MANIFEST_LOCK:
        manifest = load_manifest(project)
        entry.setdefault(
            "created_at", datetime.now(UTC).isoformat(timespec="seconds")
        )
        manifest["entries"].append(entry)
        validate_manifest(manifest)
        for sub in ("generated", "working", "exports", "references"):
            (project_dir(project) / sub).mkdir(parents=True, exist_ok=True)
        with manifest_path(project).open("w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return entry


def spend_total(project: str) -> float:
    entries = load_manifest(project).get("entries", [])
    return round(sum(entry.get("cost_usd", 0.0) for entry in entries), 4)


def spend_today(project: str) -> float:
    today = datetime.now(UTC).date().isoformat()
    entries = load_manifest(project).get("entries", [])
    spent = sum(
        entry.get("cost_usd", 0.0)
        for entry in entries
        if str(entry.get("created_at", "")).startswith(today)
    )
    return round(spent, 4)


def budget_check(project: str, planned_usd: float = 0.0) -> tuple[bool, list[str]]:
    """Hard gate: day and project limits from config/budget.yaml."""
    limits = load_budget()["limits"]
    today, total = spend_today(project), spend_total(project)
    problems = []
    if today + planned_usd > limits["per_day"]:
        problems.append(
            f"day limit: spent today {today:.2f} + planned {planned_usd:.2f}"
            f" > {limits['per_day']:.2f} USD"
        )
    if total + planned_usd > limits["per_project"]:
        problems.append(
            f"project limit: spent {total:.2f} + planned {planned_usd:.2f}"
            f" > {limits['per_project']:.2f} USD"
        )
    return not problems, problems
