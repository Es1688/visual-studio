#!/usr/bin/env python3
"""Deterministic QC: manifest validation (P0) + image checks (P1+).

P0: manifest matches schema/manifest.schema.json, ids unique, paths inside
their project. P1 adds resolution/aspect/format/file-size/not-blank checks;
P5 adds safe zones (see PLAN.md §9).

Usage:
  python scripts/qc_image.py --manifest projects/demo/manifest.json
  python scripts/qc_image.py --project demo
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import studio_common as sc  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest", help="path to manifest.json")
    source.add_argument("--project", help="project name under projects/")
    args = parser.parse_args(argv)

    path = Path(args.manifest) if args.manifest else sc.manifest_path(args.project)
    with path.open(encoding="utf-8") as f:
        manifest = json.load(f)

    problems: list[str] = []
    try:
        sc.validate_manifest(manifest)
    except Exception as exc:  # jsonschema.ValidationError
        problems.append(f"schema: {exc}")

    project = manifest.get("project", "")
    for entry in manifest.get("entries", []):
        if not entry["path"].startswith(f"projects/{project}/"):
            problems.append(f"{entry['id']}: path outside its project: {entry['path']}")

    if problems:
        for line in problems:
            print(f"FAIL {line}", file=sys.stderr)
        return 1
    print(f"OK manifest {path}: {len(manifest.get('entries', []))} entr(ies) valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
