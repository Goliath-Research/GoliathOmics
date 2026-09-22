"""
Migrate historical guardrail JSON schemas to the current message-based format.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


LEGACY_FIELDS = ("diagnose", "description", "meaning", "reason", "note")


def _build_message_from_legacy(metric: Dict[str, Any]) -> str:
    """Build a single user-facing message from historical fields."""
    note = str(metric.get("note", "")).strip()
    if note:
        return note

    parts: List[str] = []
    for key in LEGACY_FIELDS:
        if key == "note":
            continue
        value = str(metric.get(key, "")).strip()
        if value:
            parts.append(value.rstrip("."))

    if parts:
        return ". ".join(parts) + "."
    return "Guardrail explanation unavailable in legacy payload."


def migrate_guardrail_metric(metric: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """
    Convert one metric to the new schema.

    New shape:
      {value, normal_range, pass, message}
    """
    if not isinstance(metric, dict):
        return metric, False

    migrated: Dict[str, Any] = dict(metric)
    changed = False

    if "normal_range" not in migrated and "threshold" in migrated:
        migrated["normal_range"] = migrated.pop("threshold")
        changed = True

    if "message" not in migrated or not str(migrated.get("message", "")).strip():
        migrated["message"] = _build_message_from_legacy(migrated)
        changed = True

    for key in ("diagnose", "description", "meaning", "reason", "note"):
        if key in migrated:
            migrated.pop(key, None)
            changed = True

    # If both keys exist, keep canonical key and remove legacy threshold key.
    if "normal_range" in migrated and "threshold" in migrated:
        migrated.pop("threshold", None)
        changed = True

    return migrated, changed


def _is_metric_node(node: Any) -> bool:
    return isinstance(node, dict) and "pass" in node


def _migrate_details_node(node: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """Migrate a GuardrailMetric, or recurse into a nested heading."""
    if _is_metric_node(node):
        return migrate_guardrail_metric(node)
    changed = False
    out = dict(node)
    for key, child in node.items():
        if not isinstance(child, dict):
            continue
        migrated_child, child_changed = _migrate_details_node(child)
        if child_changed:
            out[key] = migrated_child
            changed = True
    return out, changed


def migrate_guardrails_payload(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """Migrate all metrics under payload['guardrails']['details'] (including nested headings)."""
    if not isinstance(payload, dict):
        return payload, False

    guardrails = payload.get("guardrails")
    if not isinstance(guardrails, dict):
        return payload, False

    details = guardrails.get("details")
    if not isinstance(details, dict):
        return payload, False

    changed = False
    new_payload: Dict[str, Any] = dict(payload)
    new_guardrails: Dict[str, Any] = dict(guardrails)
    new_details: Dict[str, Any] = dict(details)

    for metric_name, metric_data in details.items():
        if not isinstance(metric_data, dict):
            continue
        migrated_metric, metric_changed = _migrate_details_node(metric_data)
        if metric_changed:
            new_details[metric_name] = migrated_metric
            changed = True

    if changed:
        new_guardrails["details"] = new_details
        new_payload["guardrails"] = new_guardrails
    return new_payload, changed


def _iter_json_files(target: Path, recursive: bool = True) -> Iterable[Path]:
    if target.is_file() and target.suffix.lower() == ".json":
        yield target
        return

    if target.is_dir():
        pattern = "**/*.json" if recursive else "*.json"
        for path in sorted(target.glob(pattern)):
            if path.is_file():
                yield path


def migrate_file(path: Path, apply: bool = False, backup: bool = False) -> Tuple[bool, str]:
    """Migrate one JSON file. Returns (changed, status_message)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        return False, f"ERROR reading {path}: {e}"

    migrated, changed = migrate_guardrails_payload(payload)
    if not changed:
        return False, f"SKIP {path} (no migration needed)"

    if apply:
        try:
            if backup:
                backup_path = path.with_suffix(path.suffix + ".bak")
                with open(backup_path, "w", encoding="utf-8") as bf:
                    json.dump(payload, bf, indent=2)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(migrated, f, indent=2)
        except Exception as e:
            return False, f"ERROR writing {path}: {e}"
        return True, f"MIGRATED {path}"

    return True, f"WOULD MIGRATE {path} (dry-run)"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate historical guardrails to message-based schema.",
    )
    parser.add_argument("target", help="Path to a JSON file or directory containing JSON files")
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="When target is a directory, only scan top-level *.json files",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write migrated JSONs in place. Default is dry-run.",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="When used with --apply, write a .bak copy before overwriting each changed file.",
    )
    args = parser.parse_args()

    target = Path(args.target)
    if not target.exists():
        print(f"Error: target does not exist: {target}")
        raise SystemExit(1)

    files = list(_iter_json_files(target, recursive=not args.no_recursive))
    if not files:
        print(f"No JSON files found under: {target}")
        raise SystemExit(1)

    migrated_count = 0
    for file_path in files:
        changed, status = migrate_file(file_path, apply=args.apply, backup=args.backup)
        print(status)
        if changed:
            migrated_count += 1

    mode = "applied" if args.apply else "dry-run"
    print(f"\nScanned {len(files)} file(s); {migrated_count} file(s) {mode} for guardrail migration.")


if __name__ == "__main__":
    main()
