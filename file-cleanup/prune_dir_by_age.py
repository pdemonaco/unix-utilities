#!/usr/bin/env python3
"""Delete files older than N days under a target directory."""

import argparse
import csv
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol


class _CsvWriter(Protocol):  # pylint: disable=too-few-public-methods
    def writerow(self, row: list[Any]) -> Any:
        """Write one row to the CSV output."""


def parse_args() -> argparse.Namespace:
    """Build and return the CLI argument parser result."""
    parser = argparse.ArgumentParser(
        description="Delete files older than N days under a target directory."
    )
    parser.add_argument("target", help="Directory to clean up")
    parser.add_argument(
        "--mdays",
        type=int,
        default=8,
        help="Delete files older than this many days (default: 8)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without deleting",
    )
    parser.add_argument(
        "--prune-empty-dirs",
        action="store_true",
        help="Remove empty subdirectories after file deletion",
    )
    parser.add_argument(
        "--regex",
        action="append",
        default=[],
        metavar="PATTERN",
        help=(
            "Filename regex filter (repeatable);"
            " only matching files are deleted"
        ),
    )
    parser.add_argument(
        "--log-dir",
        default=None,
        help="Directory for the CSV log (default: target)",
    )
    parser.add_argument(
        "--log-file-name",
        default=None,
        help="CSV log filename (default: auto-generated)",
    )
    return parser.parse_args()


def validate_dirs(target: Path, log_dir: Path) -> None:
    """Exit with an error message if target or log_dir are not directories."""
    if not target.is_dir():
        print(f"error: target '{target}' is not a directory", file=sys.stderr)
        sys.exit(1)
    if not log_dir.is_dir():
        print(
            f"error: log-dir '{log_dir}' is not a directory",
            file=sys.stderr,
        )
        sys.exit(1)


def make_log_path(
    log_dir: Path, log_file_name: str | None, target: Path
) -> Path:
    """Derive the CSV log file path from the log directory and target."""
    if log_file_name:
        return log_dir / log_file_name
    sanitized = (
        str(target.resolve()).replace("/", "_").replace("\\", "_").lstrip("_")
    )
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return log_dir / f"cleanup-{sanitized}_{timestamp}.csv"


def compile_patterns(regexes: list[str]) -> list[re.Pattern[str]]:
    """Compile regex strings; warn and skip invalid patterns."""
    compiled: list[re.Pattern[str]] = []
    for pattern in regexes:
        try:
            compiled.append(re.compile(pattern))
        except re.error as exc:
            print(
                f"warning: skipping invalid regex '{pattern}': {exc}",
                file=sys.stderr,
            )
    return compiled


def matches_any(name: str, patterns: list[re.Pattern[str]]) -> bool:
    """Return True if name matches at least one compiled pattern."""
    return any(p.search(name) for p in patterns)


def write_csv_row(
    writer: _CsvWriter,
    date: str,
    file_path: Path,
    last_write_time: str,
    message: str,
) -> None:
    """Append one row to the CSV log."""
    writer.writerow([date, str(file_path), last_write_time, message])


# pylint: disable=too-many-arguments,too-many-positional-arguments
def prune_files(
    target: Path,
    cutoff: datetime,
    patterns: list[re.Pattern[str]],
    log_path: Path,
    dry_run: bool,
    writer: _CsvWriter,
) -> None:
    """Delete (or dry-run) files older than cutoff; log each action."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for file in sorted(target.rglob("*")):
        if not file.is_file():
            continue
        if file.resolve() == log_path.resolve():
            continue

        try:
            stat = file.stat()
        except OSError as exc:
            print(f"warning: cannot stat '{file}': {exc}", file=sys.stderr)
            continue

        mtime = datetime.fromtimestamp(stat.st_mtime)
        if mtime >= cutoff:
            continue

        if patterns and not matches_any(file.name, patterns):
            continue

        mtime_str = mtime.strftime("%Y-%m-%d %H:%M:%S")

        if dry_run:
            print(f"[dry-run] would delete: {file}")
            write_csv_row(writer, now_str, file, mtime_str, "dry-run")
            continue

        try:
            file.unlink()
            write_csv_row(writer, now_str, file, mtime_str, "deleted")
        except OSError as exc:
            msg = f"error deleting: {exc}"
            print(f"warning: {msg} '{file}'", file=sys.stderr)
            write_csv_row(writer, now_str, file, mtime_str, msg)


def prune_empty_dirs(
    target: Path,
    dry_run: bool,
    writer: _CsvWriter,
) -> None:
    """Remove empty subdirectories deepest-first; log each action."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    subdirs = sorted(
        (d for d in target.rglob("*") if d.is_dir()),
        key=lambda d: len(d.parts),
        reverse=True,
    )
    for d in subdirs:
        try:
            if any(d.iterdir()):
                continue
        except OSError as exc:
            print(f"warning: cannot read dir '{d}': {exc}", file=sys.stderr)
            continue

        if dry_run:
            print(f"[dry-run] would remove empty dir: {d}")
            write_csv_row(writer, now_str, d, "", "dry-run empty dir")
            continue

        try:
            d.rmdir()
            write_csv_row(writer, now_str, d, "", "removed empty dir")
        except OSError as exc:
            msg = f"error removing dir: {exc}"
            print(f"warning: {msg} '{d}'", file=sys.stderr)
            write_csv_row(writer, now_str, d, "", msg)


def main() -> None:
    """Entry point."""
    args = parse_args()

    target = Path(args.target)
    log_dir = Path(args.log_dir) if args.log_dir else target
    validate_dirs(target, log_dir)

    log_path = make_log_path(log_dir, args.log_file_name, target)

    if log_path.exists():
        log_path.unlink()

    cutoff = datetime.now() - timedelta(days=args.mdays)
    patterns = compile_patterns(args.regex)

    with log_path.open("w", newline="") as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_ALL)
        writer.writerow(["Date", "File", "LastWriteTime", "Message"])

        prune_files(target, cutoff, patterns, log_path, args.dry_run, writer)

        if args.prune_empty_dirs:
            prune_empty_dirs(target, args.dry_run, writer)

    print(f"Log: {log_path}")


if __name__ == "__main__":
    main()
