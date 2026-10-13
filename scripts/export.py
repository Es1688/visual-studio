#!/usr/bin/env python3
"""Export an asset with platform presets: resize with cover crop, convert format.

The source file is taken from the asset's manifest record; the result is a NEW
asset in projects/<p>/working/ with parents=[source id] — exports/ is filled
only by a human after approval (AGENTS.md). Preset sizes live in
config/capabilities.yaml -> export_presets. Local operation: cost_usd is 0.

Usage:
  python scripts/export.py --project P --asset ID
      --preset square_1x1|portrait_3x4|portrait_4x5|portrait_9x16|landscape_16x9
      [--format png|jpg|webp] [--quality 85]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import studio_common as sc  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402

PRESET_CHOICES = [
    "square_1x1", "portrait_3x4", "portrait_4x5", "portrait_9x16", "landscape_16x9"
]
SAVE_FORMATS = {"png": "PNG", "jpg": "JPEG", "webp": "WEBP"}
LOSSY_FORMATS = ("jpg", "webp")  # these are flattened to RGB and take --quality


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--preset", required=True, choices=PRESET_CHOICES)
    parser.add_argument("--format", default="png", choices=["png", "jpg", "webp"])
    parser.add_argument("--quality", type=int, default=85)
    return parser.parse_args(argv)


def find_entry(manifest: dict, asset_id: str) -> dict | None:
    for entry in manifest.get("entries", []):
        if entry.get("id") == asset_id:
            return entry
    return None


def preset_size(preset: str) -> tuple[int, int]:
    """Preset dimensions from config/capabilities.yaml -> export_presets."""
    presets = sc.load_capabilities().get("export_presets") or {}
    spec = presets.get(preset)
    if not isinstance(spec, dict) or "width" not in spec or "height" not in spec:
        known = ", ".join(sorted(presets)) or "none defined"
        raise KeyError(f"preset '{preset}' missing in export_presets; known: {known}")
    return int(spec["width"]), int(spec["height"])


def to_rgb(image: Image.Image) -> Image.Image:
    """Flatten alpha onto white; jpg/webp targets are always RGB."""
    if image.mode == "RGB":
        return image
    has_alpha = image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if has_alpha:
        rgba = image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        return flat
    return image.convert("RGB")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    manifest = sc.load_manifest(args.project)
    source = find_entry(manifest, args.asset)
    if source is None:
        print(
            f"Asset '{args.asset}' not found in projects/{args.project}/manifest.json",
            file=sys.stderr,
        )
        return 2

    src_path = sc.REPO_ROOT / source["path"]
    if not src_path.is_file():
        print(f"Asset '{args.asset}' has no file to export: {source['path']}", file=sys.stderr)
        return 2

    try:
        width, height = preset_size(args.preset)
    except KeyError as exc:
        print(f"config/capabilities.yaml export_presets: {exc.args[0]}", file=sys.stderr)
        return 2

    asset_id = sc.next_asset_id(args.project)
    rel_path = f"projects/{args.project}/working/{asset_id}-{args.preset}.{args.format}"
    out_path = sc.REPO_ROOT / rel_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with Image.open(src_path) as src:
            src.load()
            result = ImageOps.fit(src, (width, height), Image.Resampling.LANCZOS)
            if args.format in LOSSY_FORMATS:
                result = to_rgb(result)
            save_kwargs = {"quality": args.quality} if args.format in LOSSY_FORMATS else {}
            result.save(out_path, format=SAVE_FORMATS[args.format], **save_kwargs)
    except Exception as exc:
        out_path.unlink(missing_ok=True)  # do not leave a partial file behind
        print(f"Export of '{args.asset}' failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 2

    sc.append_entry(
        args.project,
        {
            "id": asset_id,
            "path": rel_path,
            "type": "image",
            "provider": None,
            "model": None,
            "api_version": None,
            "operation": "export",
            "prompt": None,
            "seed": None,
            "params": {
                "preset": args.preset,
                "format": args.format,
                "quality": args.quality,
            },
            "parents": [args.asset],
            "cost_usd": 0.0,
            "qc": {"status": "pending", "checks": []},
        },
    )
    print(f"exported {args.asset} -> {rel_path} ({width}x{height} {args.format})")
    print(f"manifest entry '{asset_id}' -> projects/{args.project}/manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
