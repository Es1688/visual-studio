#!/usr/bin/env python3
"""Generate images: brief -> prompt -> model -> estimate -> generate -> manifest.

Real runs (P1) go through the fal.ai queue API (scripts/fal_adapter.py):
FAL_KEY check -> estimate -> budget gate -> submit/poll/download -> file in
projects/<p>/generated/ + manifest record. --dry-run keeps the P0 behaviour:
estimate + planned manifest record, no API call.

Exit codes: 0 ok; 2 bad model/operation, no FAL_KEY or budget exceeded;
4 fal.ai (provider) error after retries.

Usage:
  python scripts/gen_image.py --project demo --prompt "..." [--model flux-dev]
      [--aspect square|landscape_16_9|portrait_9_16|portrait_4_5|portrait_3_4] [--seed N]
      [--ref projects/demo/references/item.png ...] [--count N] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fal_adapter  # noqa: E402
import studio_common as sc  # noqa: E402

ASPECTS = ["square", "landscape_16_9", "portrait_9_16", "portrait_4_5", "portrait_3_4"]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--model", default="flux-dev")
    parser.add_argument("--aspect", default="square", choices=ASPECTS)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--ref",
        action="append",
        default=[],
        help="Reference image path(s), repo-relative",
    )
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Estimate cost and record a planned entry, no API call",
    )
    return parser.parse_args(argv)


def aspect_pixels(aspect: str) -> tuple[int, int]:
    """Pixel size for an aspect preset (see studio_common.aspect_pixels)."""
    return sc.aspect_pixels(aspect)


def build_payload(args: argparse.Namespace, model: dict) -> dict:
    """CLI args + model config -> fal.ai input schema for text_to_image."""
    width, height = aspect_pixels(args.aspect)
    payload: dict = {
        "prompt": args.prompt,
        "image_size": {"width": width, "height": height},
        "num_images": args.count,
    }
    if args.seed is not None and model.get("seed_supported"):
        payload["seed"] = args.seed
    return payload


def main(argv: list[str] | None = None, *, client: fal_adapter.FalClient | None = None) -> int:
    args = parse_args(argv)
    model = sc.find_model(args.model)
    if "text_to_image" not in model.get("operations", []):
        print(f"Model '{args.model}' does not support text_to_image", file=sys.stderr)
        return 2

    if not args.dry_run and client is None and not os.environ.get("FAL_KEY"):
        print("FAL_KEY is not set; use --dry-run for estimate", file=sys.stderr)
        return 2

    planned = sc.estimate_cost(args.model, args.count, aspect=args.aspect)
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

    if args.dry_run:
        asset_id = sc.next_asset_id(args.project)
        entry = {
            "id": asset_id,
            "path": f"projects/{args.project}/generated/{asset_id}.png",
            "type": "image",
            "provider": model["provider"],
            "model": model["id"],
            "api_version": None,
            "operation": "text_to_image",
            "prompt": args.prompt,
            "seed": args.seed,
            "params": {
                "aspect": args.aspect,
                "count": args.count,
                "ref_images": args.ref,
                "planned_cost_usd": planned,
            },
            "parents": [],
            "cost_usd": 0.0,
            "qc": {"status": "pending", "checks": []},
            "dry_run": True,
        }
        sc.append_entry(args.project, entry)
        print(f"dry-run: planned {planned:.4f} USD for {args.count} x {args.model}{note}")
        print(f"manifest entry '{asset_id}' -> projects/{args.project}/manifest.json")
        return 0

    if args.ref:
        print(
            "--ref is applied in image_to_image (P3); ignoring for text_to_image",
            file=sys.stderr,
        )

    if client is None:
        client = fal_adapter.FalClient()

    try:
        with client:
            images = client.generate(model["endpoint"], build_payload(args, model))
    except fal_adapter.FalError as exc:
        print(f"fal.ai error: {exc}", file=sys.stderr)
        return 4

    asset_id = sc.next_asset_id(args.project)
    out_dir = sc.project_dir(args.project) / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{asset_id}.png").write_bytes(images[0])
    for index, blob in enumerate(images[1:], start=2):
        (out_dir / f"{asset_id}-{index:02d}.png").write_bytes(blob)

    entry = {
        "id": asset_id,
        "path": f"projects/{args.project}/generated/{asset_id}.png",
        "type": "image",
        "provider": model["provider"],
        "model": model["id"],
        "api_version": None,
        "operation": "text_to_image",
        "prompt": args.prompt,
        "seed": args.seed,
        "params": {
            "aspect": args.aspect,
            "count": args.count,
            "ref_images": args.ref,
            "endpoint": model["endpoint"],
            "seed": args.seed,
        },
        "parents": [],
        # fal.ai queue API does not report actual per-request cost, so the
        # capabilities.yaml estimate is recorded as the spend (PLAN.md §5, §8).
        "cost_usd": planned,
        "qc": {"status": "pending", "checks": []},
    }
    sc.append_entry(args.project, entry)
    print(f"generated {len(images)} image(s) -> projects/{args.project}/generated/{asset_id}.png")
    print(f"manifest entry '{asset_id}' -> projects/{args.project}/manifest.json")
    print(f"cost ~{planned:.4f} USD (capabilities estimate){note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
