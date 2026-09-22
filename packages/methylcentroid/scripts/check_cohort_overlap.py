#!/usr/bin/env python3
"""
Check for overlapping sample IDs between two cohort centroid configs (e.g. healthy vs cancer).

If the same sample appears in both configs, the centroid built from the "healthy" config
would be contaminated (mixed healthy + cancer), leading to invalid DMP detection and
classifier validation failure (e.g. 0% correct for class 0).

Usage:
  python scripts/check_cohort_overlap.py configs/PCa_Healthy_centroid_batch_config.json configs/PCa_Cancer_centroid_batch_config.json
  python scripts/check_cohort_overlap.py --basenames configs/healthy.json configs/cancer.json
"""

import argparse
import json
import sys
from pathlib import Path


def _sample_id(path: str) -> str:
    """Normalize path to a single sample ID (basename, strip trailing slash)."""
    return path.rstrip("/").split("/")[-1]


def _load_add_samples(config_path: Path) -> list:
    """Load add_samples from a batch config (base_config.add_samples)."""
    with open(config_path) as f:
        data = json.load(f)
    base = data.get("base_config") or data
    samples = base.get("add_samples") or []
    return [s for s in samples if isinstance(s, str) and s.strip()]


def check_overlap(
    config_a: Path,
    config_b: Path,
    label_a: str = "cohort_A",
    label_b: str = "cohort_B",
    use_basenames: bool = True,
) -> dict:
    """
    Compare sample lists from two configs. Returns dict with:
      overlap: list of sample IDs that appear in both
      only_a, only_b: IDs only in A or B
      ids_a, ids_b: full sets of IDs from each config
    """
    raw_a = _load_add_samples(config_a)
    raw_b = _load_add_samples(config_b)

    if use_basenames:
        ids_a = {_sample_id(p) for p in raw_a}
        ids_b = {_sample_id(p) for p in raw_b}
    else:
        ids_a = {p.rstrip("/") for p in raw_a}
        ids_b = {p.rstrip("/") for p in raw_b}

    overlap = ids_a & ids_b
    only_a = ids_a - ids_b
    only_b = ids_b - ids_a

    return {
        "overlap": sorted(overlap),
        "only_a": sorted(only_a),
        "only_b": sorted(only_b),
        "ids_a": ids_a,
        "ids_b": ids_b,
        "n_a": len(ids_a),
        "n_b": len(ids_b),
        "n_overlap": len(overlap),
    }


def main():
    ap = argparse.ArgumentParser(
        description="Check for overlapping samples between two cohort centroid configs."
    )
    ap.add_argument(
        "config_a",
        type=Path,
        help="First config (e.g. healthy batch config)",
    )
    ap.add_argument(
        "config_b",
        type=Path,
        help="Second config (e.g. cancer batch config)",
    )
    ap.add_argument(
        "--label-a",
        default="cohort_A",
        help="Label for first config (e.g. healthy)",
    )
    ap.add_argument(
        "--label-b",
        default="cohort_B",
        help="Label for second config (e.g. cancer)",
    )
    ap.add_argument(
        "--basenames",
        action="store_true",
        default=True,
        help="Compare by path basename only (default: True)",
    )
    ap.add_argument(
        "--full-paths",
        action="store_true",
        help="Compare by full path (disable basename matching)",
    )
    args = ap.parse_args()

    if not args.config_a.exists():
        print(f"Error: {args.config_a} not found", file=sys.stderr)
        sys.exit(1)
    if not args.config_b.exists():
        print(f"Error: {args.config_b} not found", file=sys.stderr)
        sys.exit(1)

    use_basenames = not args.full_paths
    result = check_overlap(
        args.config_a,
        args.config_b,
        label_a=args.label_a,
        label_b=args.label_b,
        use_basenames=use_basenames,
    )

    print(f"\n{'='*60}")
    print("Cohort overlap check (by basename)" if use_basenames else "Cohort overlap check (full path)")
    print(f"  {args.label_a}: {args.config_a.name}  -> {result['n_a']} samples")
    print(f"  {args.label_b}: {args.config_b.name}  -> {result['n_b']} samples")
    print("="*60)

    if result["n_overlap"] > 0:
        print(f"\n⚠️  OVERLAP: {result['n_overlap']} sample(s) appear in BOTH configs.")
        print("   Building a centroid from one config with these samples will mix cohorts and")
        print("   can cause invalid centroids and classifier validation failure (e.g. 0%% class 0).")
        print("\n   Overlapping sample IDs:")
        for sid in result["overlap"][:50]:
            print(f"     - {sid}")
        if len(result["overlap"]) > 50:
            print(f"     ... and {len(result['overlap']) - 50} more.")
        print(f"\n   Only in {args.label_a}: {len(result['only_a'])}")
        print(f"   Only in {args.label_b}: {len(result['only_b'])}")
        sys.exit(1)
    else:
        print("\n✅ No overlap: no sample ID appears in both configs.")
        print(f"   Only in {args.label_a}: {len(result['only_a'])}")
        print(f"   Only in {args.label_b}: {len(result['only_b'])}")
        sys.exit(0)


if __name__ == "__main__":
    main()
