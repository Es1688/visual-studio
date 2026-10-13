#!/usr/bin/env python3
"""Deterministic overlay of text, logo and CTA over an image via HTML templates.

Fills templates/<name>.html placeholders ({{background}}, {{headline}}, {{sub}},
{{cta}}, {{logo}}) with the data file and the background asset from the manifest,
then renders it with headless Chromium (Playwright, device_scale_factor=1) at
export-preset sizes from config/capabilities.yaml (ADR-0003). The model never
draws text/logos (AGENTS.md, «Принципы генерации»).

Templates may declare further {{word}} placeholders; each takes its value from
the same data.json (escaped as plain text), so bespoke slides (spec rows,
badges) stay data-driven. A {{word}} with no matching data key is an error.

Usage:
  python scripts/compose.py --project demo --asset demo-img-0001
      --template templates/banner.html --data data.json --ratio 1x1,4x5,9x16

data.json: {"headline": "...", "sub": "...", "cta": "...", "logo": "path-or-null",
            "<any-placeholder>": "..."}
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import studio_common as sc  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

# CLI ratio -> export_presets key in config/capabilities.yaml
RATIO_PRESETS = {
    "1x1": "square_1x1",
    "4x5": "portrait_4x5",
    "9x16": "portrait_9x16",
    "16x9": "landscape_16x9",
    "3x2": "kwork_cover",
    "57x10": "course_pc",
    "5x2": "course_mobile",
}

DATA_FIELDS = ("headline", "sub", "cta")
PLACEHOLDER_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")
CHROMIUM_HINT = "Chromium is not installed; run: uv run playwright install chromium"


class ComposeError(Exception):
    """User-facing error: missing inputs or broken environment (exit 2)."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--asset", required=True, help="background asset id from the manifest")
    parser.add_argument("--template", required=True, help="HTML template path, repo-relative")
    parser.add_argument("--data", required=True, help="JSON with headline/sub/cta/logo fields")
    parser.add_argument(
        "--ratio",
        default="1x1,4x5,9x16",
        help=f"comma-separated ratios: {','.join(RATIO_PRESETS)}",
    )
    return parser.parse_args(argv)


def fail(msg: str) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return 2


def repo_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else sc.REPO_ROOT / path


def parse_ratios(raw: str) -> list[str]:
    ratios = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = [item for item in ratios if item not in RATIO_PRESETS]
    if not ratios or unknown:
        raise ComposeError(
            f"unknown ratio(s) {unknown or ['<empty>']}; supported: {', '.join(RATIO_PRESETS)}"
        )
    return ratios


def load_data(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ComposeError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ComposeError(f"data must be a JSON object: {path}")
    return data


def find_entry(manifest: dict, asset_id: str) -> dict | None:
    for entry in manifest.get("entries", []):
        if entry["id"] == asset_id:
            return entry
    return None


def logo_tag(value: object) -> str:
    """Logo field -> <img> tag; empty/null -> '' (the slot is hidden via CSS)."""
    if not value:
        return ""
    path = repo_path(str(value))
    if not path.is_file():
        raise ComposeError(f"logo file not found: {path}")
    return f'<img class="logo" src="{path.resolve().as_uri()}" alt="logo">'


def fill_template(template: str, background: Path, data: dict) -> str:
    values = {field: html.escape(str(data.get(field) or "")) for field in DATA_FIELDS}
    values["background"] = background.resolve().as_uri()
    values["logo"] = logo_tag(data.get("logo"))

    def extra(match: re.Match) -> str:
        key = match.group(1)
        if key in ("background", "logo"):
            return match.group(0)  # handled by the typed values below
        if key in data and data[key] is not None:
            return html.escape(str(data[key]))
        return match.group(0)  # unknown key -> keep, reported below

    filled = PLACEHOLDER_RE.sub(extra, template)
    for key, value in values.items():
        filled = filled.replace("{{" + key + "}}", value)
    leftovers = sorted(set(PLACEHOLDER_RE.findall(filled)))
    if leftovers:
        raise ComposeError(
            f"data.json has no value(s) for placeholder(s): {', '.join(leftovers)}"
        )
    return filled


def render_ratios(html_doc: str, ratios: list[str]) -> list[dict]:
    """Render one PNG (bytes) per ratio; nothing is written to the repo on failure."""
    presets = sc.load_capabilities()["export_presets"]
    with tempfile.TemporaryDirectory(prefix="compose-") as tmp:
        page_path = Path(tmp) / "banner.html"
        page_path.write_text(html_doc, encoding="utf-8")
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as exc:
                if "playwright install" in str(exc) or "Executable doesn't exist" in str(exc):
                    raise ComposeError(CHROMIUM_HINT) from exc
                raise
            try:
                rendered = []
                for ratio in ratios:
                    preset = presets[RATIO_PRESETS[ratio]]
                    page = browser.new_page(
                        viewport={"width": preset["width"], "height": preset["height"]},
                        device_scale_factor=1,
                    )
                    try:
                        page.goto(page_path.as_uri(), wait_until="load")
                        png = page.screenshot(type="png")
                    finally:
                        page.close()
                    rendered.append(
                        {"ratio": ratio, "png": png, "width": preset["width"],
                         "height": preset["height"]}
                    )
            finally:
                browser.close()
    return rendered


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        ratios = parse_ratios(args.ratio)

        template_path = repo_path(args.template)
        if not template_path.is_file():
            raise ComposeError(f"template not found: {template_path}")
        data_path = repo_path(args.data)
        if not data_path.is_file():
            raise ComposeError(f"data file not found: {data_path}")
        data = load_data(data_path)

        manifest = sc.load_manifest(args.project)
        parent = find_entry(manifest, args.asset)
        if parent is None:
            raise ComposeError(
                f"asset '{args.asset}' not found in projects/{args.project}/manifest.json"
            )
        background = sc.REPO_ROOT / parent["path"]
        if not background.is_file():
            raise ComposeError(f"background file not found: {background}")

        html_doc = fill_template(
            template_path.read_text(encoding="utf-8"), background, data
        )
        rendered = render_ratios(html_doc, ratios)

        working = sc.project_dir(args.project) / "working"
        working.mkdir(parents=True, exist_ok=True)
        for item in rendered:
            out_name = f"{args.asset}-cmp-{item['ratio']}.png"
            (working / out_name).write_bytes(item["png"])
            entry = {
                "id": sc.next_asset_id(args.project, "cmp"),
                "path": f"projects/{args.project}/working/{out_name}",
                "type": "image",
                "provider": None,
                "model": None,
                "api_version": None,
                "operation": "compose",
                "prompt": None,
                "seed": None,
                "params": {
                    "template": args.template,
                    "ratio": item["ratio"],
                    "data": data,
                },
                "parents": [args.asset],
                "cost_usd": 0.0,
                "qc": {"status": "pending", "checks": []},
            }
            sc.append_entry(args.project, entry)
            print(
                f"composed {entry['id']} -> {entry['path']}"
                f" ({item['width']}x{item['height']})"
            )
    except ComposeError as exc:
        return fail(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
