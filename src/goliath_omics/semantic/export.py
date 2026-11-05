"""Semantic catalog export stub (R3 implements full export)."""

from __future__ import annotations

import argparse
import json
import sys

from goliath_omics.semantic.catalog import OMICS_NAMESPACE, SEMANTIC_CATALOG, SEMANTIC_REQUESTS


def export_catalog() -> dict[str, object]:
    return {
        "namespace": OMICS_NAMESPACE,
        "schema_version": "0",
        "entities": [],
        "requests": [],
        "note": "R2 shell; register entities in R3",
        "catalog_len": len(SEMANTIC_CATALOG),
        "requests_len": len(SEMANTIC_REQUESTS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export GoliathOmics semantic catalog JSON (R2 stub).")
    parser.add_argument(
        "--out",
        type=str,
        default="",
        help="Write JSON to this path (default: stdout)",
    )
    args = parser.parse_args(argv)
    payload = export_catalog()
    text = json.dumps(payload, indent=2) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
