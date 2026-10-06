#!/usr/bin/env python3
"""Upscale an image (default model: esrgan via fal.ai).

Stage status: stub, implemented in P3 (PLAN.md §10). The CLI is fixed now so
skills and docs can rely on it.

Usage:
  python scripts/upscale.py --project P --asset ID [--scale 2] [--model esrgan]
"""

from __future__ import annotations

import argparse
import sys

STUB = "upscale is implemented in stage P3; see PLAN.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--scale", type=int, default=2, choices=[2, 4])
    parser.add_argument("--model", default="esrgan")
    parser.parse_args(argv)
    print(STUB, file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
