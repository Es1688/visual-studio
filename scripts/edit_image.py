#!/usr/bin/env python3
"""Edit images: reference-driven edit, inpainting, background removal.

Stage P3 (PLAN.md §10). The source image comes from the asset's manifest
record; the result is a NEW asset in projects/<p>/working/ with
parents=[source] (AGENTS.md: nothing is improved in place). Models are
looked up by operation in config/capabilities.yaml, never hardcoded:

- edit              -> flux-pro-kontext (edit_reference): image_url (source
                       as data URI) + prompt; aspect_ratio ONLY when
                       --aspect is passed — the fal default keeps
                       match_input_image;
- inpaint           -> flux-fill: image_url + mask_url (--mask required) +
                       prompt; billed per MP, so the estimate uses the
                       actual source dimensions (fal keeps the aspect but
                       may downscale the output);
- remove_background -> birefnet: image_url + output_format png (free).

Every paid call is estimated and budget-gated first (config/budget.yaml).
Exit codes: 0 ok; 2 configuration (missing asset/file/prompt/mask, no
FAL_KEY, budget exceeded, no model for the operation); 4 fal.ai error
after retries.

Usage:
  python scripts/edit_image.py --project P --asset ID
      --mode edit|inpaint|remove_background [--prompt "..."] [--mask path]
      [--ref path ...] [--aspect square|landscape_16_9|portrait_9_16|portrait_4_5]
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

ASPECTS = ["square", "landscape_16_9", "portrait_9_16", "portrait_4_5"]
MODE_TO_OPERATION = {
    "edit": "edit_reference",
    "inpaint": "inpaint",
    "remove_background": "remove_background",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--asset", required=True)
    parser.add_argument(
        "--mode", required=True, choices=["edit", "inpaint", "remove_background"]
    )
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--mask", default=None, help="Mask image path for inpaint")
    parser.add_argument(
        "--ref",
        action="append",
        default=[],
        help="Extra reference image(s); multi-ref editing arrives in stage 2",
    )
    parser.add_argument(
        "--aspect",
        default=None,
        choices=ASPECTS,
        help="Output aspect; edit keeps match_input_image when omitted",
    )
    return parser.parse_args(argv)


def find_model_for_operation(operation: str) -> dict | None:
    for model in sc.load_capabilities().get("models", []):
        if operation in model.get("operations", []):
            return model
    return None


def find_entry(manifest: dict, asset_id: str) -> dict | None:
    for entry in manifest.get("entries", []):
        if entry.get("id") == asset_id:
            return entry
    return None


def build_payload(
    args: argparse.Namespace, model: dict, src_path: Path, mask_path: Path | None
) -> dict:
    """Mode + CLI args -> fal.ai input schema (config/capabilities.yaml)."""
    image_url = fal_adapter.to_data_uri(src_path)
    if args.mode == "edit":
        payload = {"image_url": image_url, "prompt": args.prompt}
        if args.aspect is not None:
            payload["aspect_ratio"] = model["aspect_params"][args.aspect]
        return payload
    if args.mode == "inpaint":
        payload = {
            "image_url": image_url,
            "mask_url": fal_adapter.to_data_uri(mask_path),
            "prompt": args.prompt,
        }
        # only custom_size endpoints take an explicit image_size; match_input
        # models (flux-pro/v1/fill) render at the source resolution and
        # reject the extra field
        if model.get("aspect_mode") == "custom_size":
            width, height = sc.aspect_pixels(args.aspect or "square")
            payload["image_size"] = {"width": width, "height": height}
        return payload
    return {"image_url": image_url, "output_format": "png"}


def estimate(mode: str, model: dict, src_path: Path) -> float:
    if mode == "inpaint":
        # flux-fill bills per megapixel and output ~= input, so the estimate
        # uses the actual source size, not the requested aspect preset.
        with Image.open(src_path) as image:
            megapixels = image.size[0] * image.size[1] / 1e6
        return sc.estimate_cost(model["id"], 1, megapixels=megapixels)
    # edit: per_image; remove_background: free — both flat in capabilities.
    return sc.estimate_cost(model["id"], 1)


def main(
    argv: list[str] | None = None, *, client: fal_adapter.FalClient | None = None
) -> int:
    args = parse_args(argv)
    operation = MODE_TO_OPERATION[args.mode]
    model = find_model_for_operation(operation)
    if model is None:
        print(
            f"No model for operation '{operation}' in config/capabilities.yaml",
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

    if args.mode in ("edit", "inpaint") and not args.prompt:
        print(f"--prompt is required for --mode {args.mode}", file=sys.stderr)
        return 2

    mask_path = None
    if args.mode == "inpaint":
        if not args.mask:
            print("--mask is required for --mode inpaint", file=sys.stderr)
            return 2
        mask_path = Path(args.mask)
        if not mask_path.is_absolute():
            mask_path = sc.REPO_ROOT / mask_path
        if not mask_path.is_file():
            print(f"Mask file not found: {args.mask}", file=sys.stderr)
            return 2

    if args.mode == "edit" and args.ref:
        print(
            "multi-reference editing arrives in stage 2; ignoring --ref"
            " (the source asset itself is the reference)",
            file=sys.stderr,
        )

    if client is None and not os.environ.get("FAL_KEY"):
        print("FAL_KEY is not set", file=sys.stderr)
        return 2

    try:
        planned = estimate(args.mode, model, src_path)
    except OSError as exc:
        print(f"Cannot read source image '{args.asset}': {exc}", file=sys.stderr)
        return 2

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

    if client is None:
        client = fal_adapter.FalClient()

    try:
        with client:
            images = client.generate(
                model["endpoint"], build_payload(args, model, src_path, mask_path)
            )
    except fal_adapter.FalError as exc:
        print(f"fal.ai error: {exc}", file=sys.stderr)
        return 4

    asset_id = sc.next_asset_id(args.project)
    rel_path = f"projects/{args.project}/working/{asset_id}.png"
    out_path = sc.REPO_ROOT / rel_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(images[0])

    params: dict = {"mode": args.mode, "endpoint": model["endpoint"]}
    if args.mode == "inpaint":
        params["mask"] = args.mask
        if args.aspect is not None:
            params["aspect"] = args.aspect  # drives image_size on custom_size models
    elif args.aspect is not None:
        params["aspect"] = args.aspect

    sc.append_entry(
        args.project,
        {
            "id": asset_id,
            "path": rel_path,
            "type": "image",
            "provider": model["provider"],
            "model": model["id"],
            "api_version": None,
            "operation": operation,
            "prompt": args.prompt,
            "seed": None,
            "params": params,
            "parents": [args.asset],
            # fal.ai does not report the actual per-request price, so the
            # capabilities.yaml estimate is recorded as the spend (as in gen_image).
            "cost_usd": planned,
            "qc": {"status": "pending", "checks": []},
        },
    )
    print(f"{args.mode} {args.asset} -> {rel_path}")
    print(f"manifest entry '{asset_id}' -> projects/{args.project}/manifest.json")
    print(f"cost ~{planned:.4f} USD (capabilities estimate){note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
