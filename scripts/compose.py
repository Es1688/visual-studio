#!/usr/bin/env python3
"""Deterministic overlay of text, logo and CTA over an image via HTML templates.

Renders templates/<name>.html with headless Chromium (Playwright) at the
requested aspect ratios. The model never draws text/logos (AGENTS.md,
«Принципы генерации»).

Stage status: stub, implemented in P4 together with templates/ and the
playwright dependency (PLAN.md §10). The CLI is fixed now so skills and docs
can rely on it.

Usage:
  python scripts/compose.py --project P --asset ID --template templates/sale.html
      --data data.json --ratio 1x1,4x5,9x16
"""

from __future__ import annotations

import argparse
import sys

STUB = "compose is implemented in stage P4; see PLAN.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--template", required=True)
    parser.add_argument("--data", required=True, help="JSON with text/CTA/logo fields")
    parser.add_argument("--ratio", default="1x1,4x5,9x16")
    parser.parse_args(argv)
    print(STUB, file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
