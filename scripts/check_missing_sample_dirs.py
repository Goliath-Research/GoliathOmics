#!/usr/bin/env python3
"""
Print sample IDs from a CSV that do not exist under /work/samples/<sample>.

Default input CSV: /work/projects/prostate-cancer/data/test.csv
Default samples base: /work/samples
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Print sample IDs whose sample directory is missing.",
    )
    p.add_argument(
        "--csv",
        default="/work/projects/prostate-cancer/data/test.csv",
        help="CSV file with a sample column (default: /work/projects/prostate-cancer/data/test.csv)",
    )
    p.add_argument(
        "--samples-base",
        default="/work/samples",
        help="Base directory containing sample folders (default: /work/samples)",
    )
    return p.parse_args()


def find_sample_column(header: list[str]) -> int:
    lower = [h.strip().lower() for h in header]
    for key in ("sample", "sample_id", "sample_path", "path"):
        if key in lower:
            return lower.index(key)
    return 0


def main() -> int:
    args = parse_args()
    csv_path = Path(args.csv)
    samples_base = Path(args.samples_base)

    if not csv_path.is_file():
        print(f"CSV not found: {csv_path}", file=sys.stderr)
        return 2
    if not samples_base.is_dir():
        print(f"Samples base not found: {samples_base}", file=sys.stderr)
        return 2

    missing: list[str] = []

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            return 0
        col = find_sample_column(header)

        for row in reader:
            if not row or col >= len(row):
                continue
            sample = row[col].strip()
            if not sample:
                continue
            if not (samples_base / sample).is_dir():
                missing.append(sample)

    for sample in missing:
        print(sample)

    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())

