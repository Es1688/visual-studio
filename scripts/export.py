#!/usr/bin/env python3
"""Export an asset with platform presets: resize, crop, format conversion.

Presets live in config/capabilities.yaml (export_presets).

Stage status: stub, minimal version lands in P1 (PLAN.md §10).

Usage:
  python scripts/export.py --project P --asset ID
      --preset square_1x1|portrait_4x5|portrait_9x16|landscape_16x9
      [--format png|jpg|webp] [--quality 85]
"""

from __future__ import annotations

import argparse
import sys

STUB = "export is implemented in stage P1; see PLAN.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--preset", required=True,
                        choices=["square_1x1", "portrait_4x5", "portrait_9x16",
                                 "landscape_16x9"])
    parser.add_argument("--format", default="png", choices=["png", "jpg", "webp"])
    parser.add_argument("--quality", type=int, default=85)
    parser.parse_args(argv)
    print(STUB, file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
