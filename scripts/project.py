#!/usr/bin/env python3
"""Project isolation control: scaffold new projects and switch the active one.

The active project is a marker file projects/.active-project (local-only:
projects/ is gitignored). While a project is active, the PreToolUse hook
scripts/hooks/project_isolation.py keeps the agent inside projects/<name>/ and
blocks writes to the studio itself; scripts confine their inputs the same way
(studio_common.ensure_project_input). `off` lifts the restriction for studio
work and cross-project audits.

Usage:
  python scripts/project.py new <name>    # scaffold projects/<name>/ + activate
  python scripts/project.py use <name>    # activate an existing project
  python scripts/project.py off           # studio mode, no active project
  python scripts/project.py status
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import studio_common as sc  # noqa: E402

BRIEF_STUB = """# Бриф: {name}

Продукт: (что и для кого)
Цель: (баннер / обложка / карточка, где будет использоваться)
Форматы: (например 1:1, 4:5)
Текст: (заголовок, цены, CTA — накладываются через compose.py, не генерируются)

Референсы заказчика складывать в references/.
"""


def scaffold(name: str) -> Path:
    root = sc.REPO_ROOT / "projects" / name
    if root.exists():
        print(
            f"Project '{name}' already exists: {root} — use `project.py use {name}`",
            file=sys.stderr,
        )
        raise SystemExit(2)
    for sub in ("references", "generated", "working", "exports", "templates"):
        (root / sub).mkdir(parents=True)
    (root / "brief.md").write_text(BRIEF_STUB.format(name=name), encoding="utf-8")
    manifest = {"project": name, "entries": []}
    sc.validate_manifest(manifest)
    with (root / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return root


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_new = sub.add_parser("new", help="scaffold projects/<name>/ and activate it")
    p_new.add_argument("name", help=f"project name, must match {sc.PROJECT_NAME_RE.pattern}")

    p_use = sub.add_parser("use", help="activate an existing project")
    p_use.add_argument("name")

    sub.add_parser("off", help="deactivate — studio mode, all projects readable")
    sub.add_parser("status", help="print the active project")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.command == "new":
        if not sc.PROJECT_NAME_RE.fullmatch(args.name):
            print(
                f"Invalid project name '{args.name}': must match"
                f" {sc.PROJECT_NAME_RE.pattern}",
                file=sys.stderr,
            )
            return 2
        try:
            root = scaffold(args.name)
        except SystemExit as exc:
            return int(exc.code or 2)
        sc.set_active(args.name)
        print(f"created {root}")
        print(f"active project: {args.name}")
        return 0

    if args.command == "use":
        try:
            sc.project_dir(args.name)  # exists-check + name sanitization
        except SystemExit as exc:
            print(exc, file=sys.stderr)
            return 2
        sc.set_active(args.name)
        print(f"active project: {args.name}")
        return 0

    if args.command == "off":
        sc.clear_active()
        print("studio mode: no active project, isolation off")
        return 0

    active = sc.active_project()
    if active is None:
        print("no active project (studio mode)")
    else:
        print(f"active project: {active}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
