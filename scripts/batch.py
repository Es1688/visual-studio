#!/usr/bin/env python3
"""Batch generation from a CSV of briefs (PLAN.md §10, P6): queue + budget.

Reads prompt/model/aspect[/count] rows, gates the WHOLE batch on the budget
in a preflight (sum of sc.estimate_cost -> sc.budget_check, exit 2 before any
API call) and only then runs scripts/gen_image.py per row in a
ThreadPoolExecutor with ONE shared fal.ai client (httpx.Client is
thread-safe; gen_image.main's signature is used as-is via client=<shared>).

Concurrency design notes:
- Per-row budget races are accepted: gen_image re-checks the budget for
  every row at run time while sibling rows append spend concurrently, so a
  row may see slightly stale numbers. The preflight gate above already
  refuses the whole batch when the SUM would exceed a day/project limit;
  only a row-to-row gap can slip through, never a mass overrun.
- gen_image draws the asset id (sc.next_asset_id) and appends the manifest
  entry in two separate steps, and reads the manifest for its budget check
  without a lock; two concurrent rows could therefore draw the SAME id or
  read a half-written manifest file. For the duration of the run this module
  swaps in wrappers (see _concurrent_manifest_safety) that serialize
  manifest IO and keep ids of in-flight rows unique; originals are restored
  afterwards. sc.append_entry itself is already thread-safe and is reused
  as-is inside the wrapper.
- gen_image closes any client it is given (`with client:`), so rows receive
  a _SharedFalClient proxy whose close()/__exit__ are no-ops; the batch
  closes the real client once, at the end, when it created it.

A bad row (unknown model, model without text_to_image, bad aspect/count,
empty prompt) is recorded as skipped and does NOT abort the batch; the exit
code becomes 1. Rows cut off by --limit are also skipped, but that is
user-intentional and does NOT affect the exit code.

Exit codes: 0 all rows ok; 1 failed and/or invalid rows; 2 preflight
refusal — CSV missing/bad header, batch budget exceeded, or no FAL_KEY
without --dry-run. Report JSON: logs/batch-<UTC timestamp>.json (gitignored).

Usage:
  python scripts/batch.py --project demo --csv projects/demo/golden.csv
      [--concurrency 2] [--dry-run] [--limit N]
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import os
import sys
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fal_adapter  # noqa: E402
import gen_image  # noqa: E402
import studio_common as sc  # noqa: E402

REQUIRED_COLUMNS = ("prompt", "model", "aspect")
PROMPT_PREVIEW_LEN = 60

# Captured at import time; _concurrent_manifest_safety swaps the module
# attributes and restores them from these references.
_ORIGINAL_NEXT_ASSET_ID = sc.next_asset_id
_ORIGINAL_APPEND_ENTRY = sc.append_entry
_ORIGINAL_LOAD_MANIFEST = sc.load_manifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--project", required=True, help="project name under projects/")
    parser.add_argument(
        "--csv", required=True, help="CSV with prompt,model,aspect[,count] rows"
    )
    parser.add_argument(
        "--concurrency", type=int, default=2, help="parallel rows (default 2)"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="run only the first N valid rows"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="estimates and planned manifest entries only, no API calls",
    )
    args = parser.parse_args(argv)
    if args.concurrency < 1:
        parser.error("--concurrency must be >= 1")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1")
    return args


class _SharedFalClient:
    """Proxy over one FalClient shared by all rows of a batch.

    gen_image.main() does `with client:` per row, which closes the client
    after the first row; the proxy makes per-row close()/__exit__ no-ops.
    The batch closes the real client itself (once, at the end) when it owns
    it; an injected client stays under the caller's control.
    """

    def __init__(self, inner: fal_adapter.FalClient) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __enter__(self) -> _SharedFalClient:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def close(self) -> None:
        """No-op: the batch owns the lifecycle (see class docstring)."""


@contextlib.contextmanager
def _concurrent_manifest_safety() -> Iterator[None]:
    """Swap sc helpers for batch-safe versions; restore them afterwards.

    - unique_next_asset_id: like sc.next_asset_id, but also skips ids handed
      out to in-flight rows (not yet appended), so two rows never draw the
      same id (a duplicate would fail schema validation inside append_entry
      and one image file would overwrite another).
    - tracked_append_entry: runs the original append_entry under the same
      lock and releases the handed-out id afterwards.
    - locked_load_manifest: every manifest read (gen_image's per-row budget
      check) goes under the same lock, so no thread reads a manifest file
      that another thread is writing.
    """
    lock = threading.RLock()
    inflight: set[str] = set()

    def unique_next_asset_id(project: str, prefix: str = "img") -> str:
        with lock:
            entries = _ORIGINAL_LOAD_MANIFEST(project).get("entries", [])
            taken = {entry["id"] for entry in entries} | inflight
            number = len(entries) + 1
            asset_id = f"{project}-{prefix}-{number:04d}"
            while asset_id in taken:
                number += 1
                asset_id = f"{project}-{prefix}-{number:04d}"
            inflight.add(asset_id)
            return asset_id

    def tracked_append_entry(project: str, entry: dict) -> dict:
        try:
            with lock:
                return _ORIGINAL_APPEND_ENTRY(project, entry)
        finally:
            with lock:
                inflight.discard(entry["id"])

    def locked_load_manifest(project: str) -> dict:
        with lock:
            return _ORIGINAL_LOAD_MANIFEST(project)

    sc.next_asset_id = unique_next_asset_id
    sc.append_entry = tracked_append_entry
    sc.load_manifest = locked_load_manifest
    try:
        yield
    finally:
        sc.next_asset_id = _ORIGINAL_NEXT_ASSET_ID
        sc.append_entry = _ORIGINAL_APPEND_ENTRY
        sc.load_manifest = _ORIGINAL_LOAD_MANIFEST


def resolve_csv_path(arg: Path) -> Path:
    """CSV location: as given when absolute; else repo-relative first, then CWD."""
    if arg.is_absolute():
        return arg
    for candidate in (sc.REPO_ROOT / arg, arg):
        if candidate.is_file():
            return candidate
    return sc.REPO_ROOT / arg  # keeps the "not found" message repo-relative


def read_csv_rows(csv_path: Path) -> list[dict[str, Any]]:
    """Parse CSV rows in file order; SystemExit on a missing file or bad header.

    Blank lines and lines whose first field starts with '#' are skipped.
    Each row dict: {row: file line number, prompt, model, aspect, count(str)}.
    """
    if not csv_path.is_file():
        raise SystemExit(f"CSV not found: {csv_path}")
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = [name.strip() for name in next(reader, [])]
        missing = [name for name in REQUIRED_COLUMNS if name not in header]
        if missing:
            raise SystemExit(
                f"bad CSV header {header}: missing column(s) {', '.join(missing)}"
            )
        index = {name: header.index(name) for name in header}

        def cell(fields: list[str], name: str) -> str:
            position = index[name]
            return fields[position].strip() if position < len(fields) else ""

        rows: list[dict[str, Any]] = []
        for fields in reader:
            if not fields or not any(field.strip() for field in fields):
                continue
            if fields[0].lstrip().startswith("#"):
                continue
            rows.append(
                {
                    "row": reader.line_num,
                    "prompt": cell(fields, "prompt"),
                    "model": cell(fields, "model"),
                    "aspect": cell(fields, "aspect"),
                    "count": cell(fields, "count"),
                }
            )
    return rows


def validate_row(row: dict[str, Any]) -> str | None:
    """Row-level error or None; normalises count to int when the row is valid."""
    if not row["prompt"]:
        return "empty prompt"
    if not row["model"]:
        return "empty model"
    try:
        model = sc.find_model(row["model"])
    except SystemExit as exc:
        return str(exc)
    if "text_to_image" not in model.get("operations", []):
        return f"model '{row['model']}' does not support text_to_image"
    if row["aspect"] not in sc.ASPECT_TO_PRESET:
        known = ", ".join(sorted(sc.ASPECT_TO_PRESET))
        return f"unknown aspect '{row['aspect']}' (known: {known})"
    try:
        count = int(row["count"] or "1")
    except ValueError:
        return f"count is not an integer: '{row['count']}'"
    if count < 1:
        return f"count must be >= 1, got {count}"
    row["count"] = count
    return None


def prompt_preview(prompt: str, limit: int = PROMPT_PREVIEW_LEN) -> str:
    """First `limit` characters of a prompt for one-line reports."""
    if len(prompt) <= limit:
        return prompt
    return prompt[: limit - 3] + "..."


def _mark_all_skipped(rows: list[dict[str, Any]], reason: str) -> None:
    for row in rows:
        row["status"] = "skipped"
        row["rc"] = None
        row["ok"] = False
        row["reason"] = row["error"] if row["error"] is not None else reason


def _report_row(row: dict[str, Any]) -> dict[str, Any]:
    out = {
        "row": row["row"],
        "prompt": prompt_preview(row["prompt"]),
        "model": row["model"],
        "aspect": row["aspect"],
        "count": row["count"],
        "estimate_usd": row["estimate_usd"],
        "status": row["status"],
        "ok": row["ok"],
        "rc": row["rc"],
    }
    if row.get("reason"):
        out["reason"] = row["reason"]
    if row.get("error") is not None:
        out["error"] = row["error"]
    return out


def _build_report(
    args: argparse.Namespace,
    rows: list[dict[str, Any]],
    started: datetime,
    total_estimate: float,
    spent: float,
    problems: list[str],
) -> dict[str, Any]:
    return {
        "project": args.project,
        "csv": args.csv,
        "mode": "dry-run" if args.dry_run else "real",
        "concurrency": args.concurrency,
        "limit": args.limit,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rows": [_report_row(row) for row in rows],
        "totals": {
            "rows": len(rows),
            "ok": sum(row["status"] == "ok" for row in rows),
            "failed": sum(row["status"] == "failed" for row in rows),
            "skipped": sum(row["status"] == "skipped" for row in rows),
            "skipped_invalid": sum(1 for row in rows if row["error"] is not None),
            "estimate_usd": total_estimate,
            "spent_usd": spent,
        },
        "budget_problems": problems,
    }


def write_report(report: dict[str, Any], started: datetime) -> Path:
    """Dump the batch report to logs/batch-<UTC timestamp>.json (gitignored)."""
    logs_dir = sc.REPO_ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y%m%dT%H%M%S.%f")
    path = logs_dir / f"batch-{stamp}Z.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def main(
    argv: list[str] | None = None, *, client: fal_adapter.FalClient | None = None
) -> int:
    args = parse_args(argv)
    started = datetime.now(UTC)

    try:
        sc.project_dir(args.project)
        csv_path = sc.ensure_project_input(
            args.project, resolve_csv_path(Path(args.csv)), "--csv"
        )
        rows = read_csv_rows(csv_path)
    except SystemExit as exc:
        print(f"PREFLIGHT FAILED: {exc}", file=sys.stderr)
        return 2

    for row in rows:
        row["error"] = validate_row(row)
        row["estimate_usd"] = (
            sc.estimate_cost(row["model"], row["count"], aspect=row["aspect"])
            if row["error"] is None
            else 0.0
        )

    valid_rows = [row for row in rows if row["error"] is None]
    run_rows = valid_rows if args.limit is None else valid_rows[: args.limit]
    planned_ids = {id(row) for row in run_rows}
    total_estimate = round(sum(row["estimate_usd"] for row in run_rows), 4)

    ok, problems = sc.budget_check(args.project, total_estimate)
    if not ok:
        print(
            "PREFLIGHT FAILED — batch estimate exceeds a budget limit,"
            " nothing was generated:",
            flush=True,
        )
        for row in rows:
            note = f"  [{row['error']}]" if row["error"] is not None else ""
            print(
                f"  row {row['row']}: {row['estimate_usd']:.4f} USD —"
                f" {prompt_preview(row['prompt'])}{note}"
            )
        for line in problems:
            print(f"BUDGET EXCEEDED: {line}")
        _mark_all_skipped(rows, "budget exceeded — batch refused before any API call")
        write_report(_build_report(args, rows, started, total_estimate, 0.0, problems), started)
        return 2

    if not args.dry_run and client is None and not os.environ.get("FAL_KEY"):
        message = (
            "FAL_KEY is not set — a real batch needs it;"
            " rerun with --dry-run for estimates only"
        )
        print(f"PREFLIGHT FAILED: {message}", file=sys.stderr)
        _mark_all_skipped(rows, message)
        write_report(_build_report(args, rows, started, total_estimate, 0.0, []), started)
        return 2

    print(
        f"preflight: {len(run_rows)} row(s) to run, estimate {total_estimate:.4f} USD"
        f" (concurrency {args.concurrency},"
        f" {'dry-run' if args.dry_run else 'real run'})",
        flush=True,
    )

    real_client = client
    owns_client = False
    shared: _SharedFalClient | None = None
    if not args.dry_run:
        if real_client is None:
            try:
                real_client = fal_adapter.FalClient()
            except RuntimeError as exc:
                print(f"PREFLIGHT FAILED: {exc}", file=sys.stderr)
                return 2
            owns_client = True
        shared = _SharedFalClient(real_client)

    entries_before = len(sc.load_manifest(args.project).get("entries", []))

    def run_row(row: dict[str, Any]) -> int | None:
        argv_row = [
            "--project",
            args.project,
            "--prompt",
            row["prompt"],
            "--model",
            row["model"],
            "--aspect",
            row["aspect"],
            "--count",
            str(row["count"]),
        ]
        if args.dry_run:
            argv_row.append("--dry-run")
        try:
            return gen_image.main(argv_row, client=None if args.dry_run else shared)
        except (Exception, SystemExit) as exc:  # a row crash must not kill the batch
            row["error"] = f"{type(exc).__name__}: {exc}"
            return None

    with _concurrent_manifest_safety():
        try:
            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                results = zip(run_rows, pool.map(run_row, run_rows), strict=True)
                for row, rc in results:
                    row["rc"] = rc
                    row["ok"] = rc == 0
                    row["status"] = "ok" if rc == 0 else "failed"
                    if rc != 0:
                        row["reason"] = f"gen_image exited with {rc}"
        finally:
            if owns_client and real_client is not None:
                real_client.close()

    for row in rows:
        if "status" not in row:  # invalid rows and rows cut off by --limit
            row["status"] = "skipped"
            row["rc"] = None
            row["ok"] = False
            if row["error"] is not None:
                row["reason"] = row["error"]
            elif id(row) not in planned_ids:
                row["reason"] = f"beyond --limit {args.limit}"
            else:
                row["reason"] = "not run"

    entries = sc.load_manifest(args.project).get("entries", [])
    spent = round(
        sum(entry.get("cost_usd", 0.0) for entry in entries[entries_before:]), 4
    )

    ok_count = sum(row["status"] == "ok" for row in rows)
    failed_count = sum(row["status"] == "failed" for row in rows)
    skipped_count = sum(row["status"] == "skipped" for row in rows)
    invalid_count = sum(1 for row in rows if row["error"] is not None)

    report = _build_report(args, rows, started, total_estimate, spent, [])
    report_path = write_report(report, started)

    for row in rows:
        if row["status"] != "ok":
            rc_text = f", rc {row['rc']}" if row["rc"] is not None else ""
            print(f"row {row['row']} {row['status'].upper()}{rc_text}: {row['reason']}")
    print(
        f"batch: {ok_count} ok, {failed_count} failed, {skipped_count} skipped;"
        f" estimate {total_estimate:.4f} USD, spent {spent:.4f} USD",
        flush=True,
    )
    print(f"report: {report_path}", flush=True)
    return 0 if failed_count == 0 and invalid_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
