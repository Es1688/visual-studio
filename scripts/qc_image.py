#!/usr/bin/env python3
"""Deterministic QC: manifest validation (P0) + image checks (P1/P5).

Level 1 of the QC pipeline (PLAN.md §9): the manifest matches
schema/manifest.schema.json and paths stay inside their project; every image
file is then checked — opens, minimum side, not blank/monochrome, file size,
aspect ratio from params.aspect. dry-run records without a file are planned,
not errors. Records with qc.status already set (via --set-status after a
manual/visual review) are reported but never re-checked automatically.

Usage:
  python scripts/qc_image.py --manifest projects/demo/manifest.json
  python scripts/qc_image.py --project demo [--min-side 256] [--min-std 3.0]
  python scripts/qc_image.py --project demo --asset ID
      --set-status pass|fail|retry [--note "..."]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import studio_common as sc  # noqa: E402
from PIL import Image, ImageStat  # noqa: E402

MIN_FILE_BYTES = 1024  # a real render is bigger than 1 KB
ASPECT_TOLERANCE = 0.02  # max relative deviation from the target ratio (2%)

ASPECT_RATIOS = {
    "square": 1.0,
    "1x1": 1.0,
    "landscape_16_9": 16 / 9,
    "16x9": 16 / 9,
    "portrait_9_16": 9 / 16,
    "9x16": 9 / 16,
    "portrait_4_5": 4 / 5,
    "4x5": 4 / 5,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest", help="path to manifest.json")
    source.add_argument("--project", help="project name under projects/")
    parser.add_argument(
        "--min-side", type=int, default=256, help="minimum image side in px (default: 256)"
    )
    parser.add_argument(
        "--min-std",
        type=float,
        default=3.0,
        help="minimum brightness std, monochrome guard (default: 3)",
    )
    parser.add_argument("--asset", help="asset id to update (requires --set-status)")
    parser.add_argument(
        "--set-status", choices=["pass", "fail", "retry"],
        help="record a manual/visual QC verdict for --asset",
    )
    parser.add_argument("--note", default="", help="note stored next to --set-status")
    return parser.parse_args(argv)


def manifest_problems(manifest: dict) -> list[str]:
    """Schema + path-inside-project validation (P0 semantics)."""
    problems: list[str] = []
    try:
        sc.validate_manifest(manifest)
    except Exception as exc:  # jsonschema.ValidationError
        problems.append(f"schema: {exc}")
    project = manifest.get("project", "")
    for entry in manifest.get("entries", []):
        rel_path = entry.get("path", "")
        if not rel_path.startswith(f"projects/{project}/"):
            problems.append(f"{entry.get('id', '?')}: path outside its project: {rel_path}")
    return problems


def load_manifest_file(path: Path) -> dict | None:
    """Read manifest.json; None (with a message) when missing or not JSON."""
    if not path.is_file():
        print(f"Manifest not found: {path}", file=sys.stderr)
        return None
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        print(f"Manifest is not valid JSON: {path}: {exc}", file=sys.stderr)
        return None


def check_image(path: Path, aspect: str | None, min_side: int, min_std: float) -> str:
    """Deterministic checks for one image; returns 'OK (...)' or 'FAIL <reasons>'."""
    failures: list[str] = []
    file_bytes = path.stat().st_size
    if file_bytes <= MIN_FILE_BYTES:
        failures.append(f"file_size ({file_bytes} bytes <= {MIN_FILE_BYTES})")
    try:
        with Image.open(path) as img:
            img.load()
            width, height = img.size
            brightness_std = ImageStat.Stat(img.convert("L")).stddev[0]
            fmt = (img.format or "unknown").lower()
    except Exception as exc:
        return f"FAIL open ({exc.__class__.__name__}: {exc})"
    if min(width, height) < min_side:
        failures.append(f"min_side ({width}x{height}, needs >= {min_side}px)")
    if brightness_std < min_std:
        failures.append(f"monochrome (brightness std {brightness_std:.2f} < {min_std})")
    if aspect is not None:
        target = ASPECT_RATIOS.get(aspect)
        if target is None:
            failures.append(f"aspect (unknown aspect '{aspect}')")
        else:
            actual = width / height
            if abs(actual - target) / target > ASPECT_TOLERANCE:
                failures.append(
                    f"aspect ({aspect}: ratio {actual:.3f} vs {target:.3f},"
                    f" deviation > {ASPECT_TOLERANCE:.0%})"
                )
    if failures:
        return "FAIL " + "; ".join(failures)
    return f"OK ({fmt} {width}x{height}, std {brightness_std:.1f})"


def report_entry(entry: dict, min_side: int, min_std: float) -> tuple[str, str]:
    """One report line plus its kind: ok | fail | planned | skipped."""
    asset_id = entry.get("id", "?")
    if entry.get("type") != "image":
        return f"{asset_id}: skipped (type={entry.get('type')})", "skipped"
    qc_status = entry.get("qc", {}).get("status")
    if qc_status != "pending":
        return f"{asset_id}: skipped (qc.status={qc_status}, not re-checked)", "skipped"
    path = sc.REPO_ROOT / entry.get("path", "")
    if not path.is_file():
        if entry.get("dry_run"):
            return f"{asset_id}: planned (no file)", "planned"
        return f"{asset_id}: FAIL file missing ({entry.get('path', '')})", "fail"
    verdict = check_image(path, entry.get("params", {}).get("aspect"), min_side, min_std)
    kind = "ok" if verdict.startswith("OK") else "fail"
    return f"{asset_id}: {verdict}", kind


def set_status(manifest_path: Path, asset_id: str, status: str, note: str) -> int:
    """Record a manual/visual verdict: update qc.status/qc.checks, rewrite manifest."""
    manifest = load_manifest_file(manifest_path)  # re-read: the file is the source of truth
    if manifest is None:
        return 1
    entry = next((e for e in manifest.get("entries", []) if e.get("id") == asset_id), None)
    if entry is None:
        print(f"Asset '{asset_id}' not found in {manifest_path}", file=sys.stderr)
        return 2
    entry.setdefault("qc", {}).setdefault("checks", []).append(
        {
            "check": "manual",
            "status": status,
            "note": note,
            "set_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
    )
    entry["qc"]["status"] = status
    problems = manifest_problems(manifest)
    if problems:
        for line in problems:
            print(f"FAIL {line}", file=sys.stderr)
        return 1
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    suffix = f" (note: {note})" if note else ""
    print(f"{asset_id}: qc.status -> {status}{suffix}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.set_status and not args.asset:
        print("--set-status requires --asset ID", file=sys.stderr)
        return 2
    if args.asset and not args.set_status:
        print("--asset is only used with --set-status", file=sys.stderr)
        return 2

    if args.manifest:
        manifest_path = Path(args.manifest)
        manifest = load_manifest_file(manifest_path)
        if manifest is None:
            return 1
    else:
        manifest_path = sc.manifest_path(args.project)
        manifest = sc.load_manifest(args.project)  # empty manifest is fine (dry start)

    problems = manifest_problems(manifest)
    if problems:
        for line in problems:
            print(f"FAIL {line}", file=sys.stderr)
        return 1

    if args.set_status:
        return set_status(manifest_path, args.asset, args.set_status, args.note)

    print(f"OK manifest {manifest_path}: {len(manifest.get('entries', []))} entr(ies) valid")
    counts = {"ok": 0, "fail": 0, "planned": 0, "skipped": 0}
    for entry in manifest.get("entries", []):
        line, kind = report_entry(entry, args.min_side, args.min_std)
        print(line)
        counts[kind] += 1
    print(
        f"QC images: {counts['ok']} ok, {counts['fail']} fail,"
        f" {counts['planned']} planned, {counts['skipped']} skipped"
    )
    return 1 if counts["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
