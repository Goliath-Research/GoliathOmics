"""Validate study manifests against SaMD profile partition / claim rules."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# Profiles that require non-empty holdout partitions before start.
_ENRICHMENT_PROFILES = frozenset({"samd_holdout_enrichment"})
_PIVOTAL_PROFILES = frozenset({"samd_pivotal"})
_PRE_PIVOTAL_STAGES = frozenset(
    {
        "feasibility",
        "expanded_development",
        "internal_validation",
        "model_freeze",
    }
)


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_manifest(
    project: Dict[str, Any],
    *,
    pipeline_profile: Optional[str] = None,
) -> List[str]:
    """Return human-readable errors (empty list means OK)."""
    errors: List[str] = []
    profile = (pipeline_profile or "").strip()
    partitions = project.get("validation_partitions") or {}
    if not isinstance(partitions, dict):
        errors.append("validation_partitions must be an object")
        partitions = {}

    def _nonempty(role: str) -> bool:
        val = partitions.get(role) or []
        return isinstance(val, list) and len(val) > 0

    if profile in _ENRICHMENT_PROFILES and not _nonempty("locked_test"):
        errors.append(
            f"profile {profile!r} requires non-empty validation_partitions.locked_test "
            "(real holdouts for enrichment)"
        )
    if profile in _PIVOTAL_PROFILES and not _nonempty("pivotal_validation"):
        errors.append(
            f"profile {profile!r} requires non-empty validation_partitions.pivotal_validation"
        )

    regulatory = project.get("regulatory") or {}
    if not isinstance(regulatory, dict):
        errors.append("regulatory must be an object")
        return errors

    stage = str(regulatory.get("stage") or "feasibility")
    allow_claims = bool(regulatory.get("allow_clinical_performance_claims", False))
    if allow_claims and stage in _PRE_PIVOTAL_STAGES:
        errors.append(
            "allow_clinical_performance_claims=true is not allowed before "
            "pivotal_validation stage (set regulatory.stage first)"
        )

    # Soft disjointness: overlapping string IDs across roles
    roles = ("development_train", "locked_test", "pivotal_validation")
    seen: Dict[str, str] = {}
    for role in roles:
        for item in partitions.get(role) or []:
            key = str(item)
            if key in seen and seen[key] != role:
                errors.append(
                    f"validation_partitions overlap: {key!r} appears in both "
                    f"{seen[key]!r} and {role!r}"
                )
            else:
                seen[key] = role

    return errors


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a study manifest for SaMD profile partition/claim rules."
    )
    parser.add_argument(
        "--project",
        type=Path,
        required=True,
        help="Path to project_*.json study manifest",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="pipelineProfile to check (samd_research|samd_holdout_enrichment|samd_pivotal)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.project.is_file():
        print(f"error: project not found: {args.project}", file=sys.stderr)
        return 2

    project = _load_json(args.project)
    errors = validate_manifest(project, pipeline_profile=args.profile)
    if errors:
        print(f"INVALID {args.project}", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print(f"OK {args.project}" + (f" (profile={args.profile})" if args.profile else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
