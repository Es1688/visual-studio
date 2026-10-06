#!/usr/bin/env python3
"""Cost estimation and budget accounting (see AGENTS.md, «Бюджет»).

Commands:
  estimate --model flux-dev [--count N]   estimated cost before a paid call
  spend --project NAME [--today]          actual spend recorded in the manifest
  check --project NAME [--planned USD]    verify limits from config/budget.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import studio_common as sc  # noqa: E402


def cmd_estimate(args: argparse.Namespace) -> int:
    model = sc.find_model(args.model)
    cost = sc.estimate_cost(args.model, args.count)
    warn = (
        "  [PRICE IS A PLACEHOLDER — verify at fal.ai/pricing]"
        if model.get("verify_price")
        else ""
    )
    print(f"{cost:.4f} USD for {args.count} x {args.model}{warn}")
    if cost > sc.confirm_threshold():
        print(
            f"Above confirm_threshold {sc.confirm_threshold():.2f} USD —"
            " ask the user before a real run."
        )
    return 0


def cmd_spend(args: argparse.Namespace) -> int:
    total = sc.spend_today(args.project) if args.today else sc.spend_total(args.project)
    window = "today" if args.today else "total"
    print(f"{total:.4f} USD ({window}, project '{args.project}')")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    limits = sc.load_budget()["limits"]
    print(
        f"day {sc.spend_today(args.project):.2f}/{limits['per_day']:.2f}"
        f"  project {sc.spend_total(args.project):.2f}/{limits['per_project']:.2f}"
        f"  planned {args.planned:.2f} USD"
    )
    ok, problems = sc.budget_check(args.project, args.planned)
    if ok:
        print("OK within budget")
        return 0
    for line in problems:
        print(f"EXCEEDED {line}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_estimate = sub.add_parser("estimate", help="estimated cost before a paid call")
    p_estimate.add_argument("--model", required=True)
    p_estimate.add_argument("--count", type=int, default=1)
    p_estimate.set_defaults(func=cmd_estimate)

    p_spend = sub.add_parser("spend", help="actual spend from the manifest")
    p_spend.add_argument("--project", required=True)
    p_spend.add_argument("--today", action="store_true")
    p_spend.set_defaults(func=cmd_spend)

    p_check = sub.add_parser("check", help="verify limits from config/budget.yaml")
    p_check.add_argument("--project", required=True)
    p_check.add_argument("--planned", type=float, default=0.0)
    p_check.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
