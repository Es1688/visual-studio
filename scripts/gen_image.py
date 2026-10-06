#!/usr/bin/env python3
"""Generate images: brief -> prompt -> model -> estimate -> generate -> manifest.

Stage status: P0 implements --dry-run only (estimate + manifest record, no API
call; cost_usd is 0, planned cost goes to params.planned_cost_usd). Real
generation via the fal.ai queue API lands in P1 (PLAN.md §10).

Usage:
  python scripts/gen_image.py --project demo --prompt "..." [--model flux-dev]
      [--aspect square|landscape_16_9|portrait_9_16|portrait_4_5] [--seed N]
      [--ref projects/demo/references/item.png ...] [--count N] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import studio_common as sc  # noqa: E402

ASPECTS = ["square", "landscape_16_9", "portrait_9_16", "portrait_4_5"]


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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    model = sc.find_model(args.model)
    if "text_to_image" not in model.get("operations", []):
        print(f"Model '{args.model}' does not support text_to_image", file=sys.stderr)
        return 2

    planned = sc.estimate_cost(args.model, args.count)
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

    if not args.dry_run:
        print(
            "Real generation is implemented in P1; run with --dry-run"
            " for estimate + manifest record.",
            file=sys.stderr,
        )
        return 3

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


if __name__ == "__main__":
    raise SystemExit(main())
