#!/usr/bin/env python3
"""Upscale an asset (default model: esrgan -> fal-ai/clarity-upscaler).

Stage P3 (PLAN.md §10). The source comes from the asset's manifest record;
the result is a NEW asset in projects/<p>/working/ with parents=[source]
and the 'up' id prefix (AGENTS.md: a new version is a new asset). esrgan
bills per OUTPUT megapixel = source MP x scale^2; source MP is measured
from the actual file (PIL.Image), so the budget estimate follows --scale.
Scale must stay inside the model's scale_range (config/capabilities.yaml).

Exit codes: 0 ok; 2 configuration (unsupported model, scale out of range,
missing asset/file, no FAL_KEY, budget exceeded); 4 fal.ai error after
retries.

Usage:
  python scripts/upscale.py --project P --asset ID [--scale 2] [--model esrgan]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fal_adapter  # noqa: E402
import studio_common as sc  # noqa: E402
from PIL import Image  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--scale", type=int, default=2)
    parser.add_argument("--model", default="esrgan")
    return parser.parse_args(argv)


def find_entry(manifest: dict, asset_id: str) -> dict | None:
    for entry in manifest.get("entries", []):
        if entry.get("id") == asset_id:
            return entry
    return None


def source_megapixels(src_path: Path) -> float:
    with Image.open(src_path) as image:
        return image.size[0] * image.size[1] / 1e6


def main(
    argv: list[str] | None = None, *, client: fal_adapter.FalClient | None = None
) -> int:
    args = parse_args(argv)
    model = sc.find_model(args.model)
    if "upscale" not in model.get("operations", []):
        print(f"Model '{args.model}' does not support upscale", file=sys.stderr)
        return 2
    scale_range = model.get("scale_range")
    if scale_range and not scale_range[0] <= args.scale <= scale_range[1]:
        print(
            f"Scale {args.scale} is outside {model['id']} scale_range"
            f" {scale_range[0]}..{scale_range[1]}",
            file=sys.stderr,
        )
        return 2

    source = find_entry(sc.load_manifest(args.project), args.asset)
    if source is None:
        print(
            f"Asset '{args.asset}' not found in projects/{args.project}/manifest.json",
            file=sys.stderr,
        )
        return 2
    src_path = sc.REPO_ROOT / source["path"]
    if not src_path.is_file():
        print(f"Asset '{args.asset}' has no source file: {source['path']}", file=sys.stderr)
        return 2

    if client is None and not os.environ.get("FAL_KEY"):
        print("FAL_KEY is not set", file=sys.stderr)
        return 2

    try:
        output_mp = source_megapixels(src_path) * args.scale**2
    except OSError as exc:
        print(f"Cannot read source image '{args.asset}': {exc}", file=sys.stderr)
        return 2

    planned = sc.estimate_cost(args.model, 1, megapixels=output_mp)
    ok, problems = sc.budget_check(args.project, planned)
    if not ok:
        for line in problems:
            print(f"BUDGET EXCEEDED: {line}", file=sys.stderr)
        return 2

    note = ""
    if planned > sc.confirm_threshold():
        note = (
            f" (above confirm_threshold {sc.confirm_threshold():.2f} USD —"
            " the agent must ask the user before a real run)"
        )

    payload = {
        "image_url": fal_adapter.to_data_uri(src_path),
        "upscale_factor": args.scale,
    }

    if client is None:
        client = fal_adapter.FalClient()

    try:
        with client:
            images = client.generate(model["endpoint"], payload)
    except fal_adapter.FalError as exc:
        print(f"fal.ai error: {exc}", file=sys.stderr)
        return 4

    asset_id = sc.next_asset_id(args.project, prefix="up")
    rel_path = f"projects/{args.project}/working/{asset_id}.png"
    out_path = sc.REPO_ROOT / rel_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(images[0])

    sc.append_entry(
        args.project,
        {
            "id": asset_id,
            "path": rel_path,
            "type": "image",
            "provider": model["provider"],
            "model": model["id"],
            "api_version": None,
            "operation": "upscale",
            "prompt": None,
            "seed": None,
            "params": {
                "scale": args.scale,
                "endpoint": model["endpoint"],
                "megapixels_output": round(output_mp, 4),
            },
            "parents": [args.asset],
            # fal.ai does not report the actual per-request price, so the
            # capabilities.yaml estimate is recorded as the spend (as in gen_image).
            "cost_usd": planned,
            "qc": {"status": "pending", "checks": []},
        },
    )
    print(f"upscaled {args.asset} x{args.scale} -> {rel_path}")
    print(f"manifest entry '{asset_id}' -> projects/{args.project}/manifest.json")
    print(f"cost ~{planned:.4f} USD (capabilities estimate){note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
