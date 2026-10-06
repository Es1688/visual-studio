#!/usr/bin/env python3
"""Edit images: reference-driven edit, inpainting, background removal.

Stage status: stub, implemented in P3 (PLAN.md §10). The CLI is fixed now so
skills and docs can rely on it.

Usage:
  python scripts/edit_image.py --project P --asset ID
      --mode edit|inpaint|remove_background [--prompt "..."] [--mask path]
      [--ref path ...]
"""

from __future__ import annotations

import argparse
import sys

STUB = "edit_image is implemented in stage P3; see PLAN.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--mode", required=True,
                        choices=["edit", "inpaint", "remove_background"])
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--mask", default=None, help="Mask image path for inpaint")
    parser.add_argument("--ref", action="append", default=[])
    parser.parse_args(argv)
    print(STUB, file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
