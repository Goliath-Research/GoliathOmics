"""
Export the unified action catalog to schemas/actions/catalog.json.

Usage:
  methyl-export-action-catalog
  methyl-export-action-catalog --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

from methyl_utils.repo_paths import repo_schemas_dir

from .action_catalog import ACTION_CATALOG, validate_catalog_linkage


def repo_schemas_actions_dir() -> Path:
    return repo_schemas_dir("actions", start=Path(__file__))


def generate_catalog_dict() -> dict:
    linkage_errors = validate_catalog_linkage()
    if linkage_errors:
        raise RuntimeError("catalog linkage invalid:\n" + "\n".join(linkage_errors))
    return {
        "version": 1,
        "actions": [entry.to_catalog_dict() for entry in ACTION_CATALOG],
    }


def catalog_to_canonical_json(catalog: dict) -> str:
    return json.dumps(catalog, indent=2, sort_keys=True) + "\n"


def export_action_catalog(*, output_root: Path | None = None, write: bool = True) -> Path:
    catalog = generate_catalog_dict()
    text = catalog_to_canonical_json(catalog)
    root = output_root if output_root is not None else repo_schemas_actions_dir()
    path = root / "catalog.json"
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return path


def check_action_catalog_drift(*, output_root: Path | None = None) -> List[str]:
    errors: List[str] = []
    root = output_root if output_root is not None else repo_schemas_actions_dir()
    path = root / "catalog.json"
    expected = catalog_to_canonical_json(generate_catalog_dict())
    if not path.is_file():
        errors.append(f"missing catalog artifact: {path} (run methyl-export-action-catalog)")
        return errors
    actual = path.read_text(encoding="utf-8")
    if actual != expected:
        errors.append(
            f"stale catalog artifact: {path} (regenerate with: methyl-export-action-catalog)"
        )
    return errors


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export unified workflow action catalog (schemas/actions/catalog.json)."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write files; exit non-zero if committed catalog differs.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Override output directory (default: <repo>/schemas/actions).",
    )
    args = parser.parse_args(argv)
    root = args.output_root.expanduser().resolve() if args.output_root else repo_schemas_actions_dir()

    if args.check:
        drift = check_action_catalog_drift(output_root=root)
        if drift:
            for msg in drift:
                print(msg, file=sys.stderr)
            print(f"Action catalog drift check failed ({len(drift)} issue(s)).", file=sys.stderr)
            return 1
        print(f"Action catalog drift check passed ({len(ACTION_CATALOG)} actions).")
        return 0

    path = export_action_catalog(output_root=root, write=True)
    print(f"Wrote catalog: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
