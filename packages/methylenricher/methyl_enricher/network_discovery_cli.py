"""
CLI for custom network discovery and curation export.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

from methyl_utils import load_project

from .network_discovery import run_network_discovery
from .project_resolver import DEFAULT_METHYL_ENRICHER_HOME, resolve_methyl_enricher_home


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover and curate custom network edges from MethylEnricher outputs."
    )
    parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="Optional project JSON. Used to resolve methyl_enricher_home and default scan root.",
    )
    parser.add_argument(
        "--scan-root",
        action="append",
        default=[],
        help="Root directory to scan for ppi_network_edges.csv/modules_ranked.csv (repeatable).",
    )
    parser.add_argument(
        "--methyl-enricher-home",
        type=str,
        default=None,
        help=f"Override cache/home root (default: {DEFAULT_METHYL_ENRICHER_HOME} or project step_config.enricher.methyl_enricher_home).",
    )
    parser.add_argument("--db-path", type=str, default=None, help="Optional explicit SQLite path.")
    parser.add_argument("--export-path", type=str, default=None, help="Optional explicit local_edges CSV path.")
    parser.add_argument("--string-species", type=int, default=9606, help="NCBI species for STRING lookup (default: 9606).")
    parser.add_argument(
        "--string-required-score-for-novelty",
        type=float,
        default=0.0,
        help="STRING minimum score for novelty lookup (default: 0.0 to maximize known-edge detection).",
    )
    parser.add_argument(
        "--refinement-score-threshold",
        type=float,
        default=400.0,
        help="Reference STRING threshold used for novelty status labeling (default: 400).",
    )
    parser.add_argument(
        "--string-cache-path",
        type=str,
        default=None,
        help="Optional STRING cache path for lookup reuse.",
    )
    parser.add_argument(
        "--min-export-score",
        type=float,
        default=0.0,
        help="Minimum custom score for curated edge CSV export.",
    )
    return parser.parse_args()


def _resolve_scan_roots(args: argparse.Namespace) -> List[Path]:
    roots: List[Path] = [Path(x).expanduser().resolve() for x in (args.scan_root or []) if str(x).strip()]
    if roots:
        return roots
    if args.project:
        project = load_project(Path(args.project))
        derived = project.get_derived_paths()
        enricher_dir = getattr(derived, "enricher_dir", None)
        if enricher_dir:
            return [Path(str(enricher_dir)).expanduser().resolve()]
    raise ValueError("No scan roots resolved. Pass --scan-root or --project with valid enricher_dir.")


def main() -> None:
    args = _parse_args()
    project_path = Path(args.project).expanduser().resolve() if args.project else None
    try:
        if args.methyl_enricher_home:
            home = str(Path(args.methyl_enricher_home).expanduser().resolve())
        elif project_path is not None:
            home = resolve_methyl_enricher_home(project_path)
        else:
            home = DEFAULT_METHYL_ENRICHER_HOME
        scan_roots = _resolve_scan_roots(args)
        project_name = None
        if project_path is not None:
            project = load_project(project_path)
            project_name = str(getattr(project, "project_name", "") or "")
        result = run_network_discovery(
            scan_roots=scan_roots,
            methyl_enricher_home=home,
            project_name=project_name,
            db_path=args.db_path,
            export_path=args.export_path,
            string_species=args.string_species,
            string_required_score_for_novelty=args.string_required_score_for_novelty,
            refinement_score_threshold=args.refinement_score_threshold,
            string_cache_path=args.string_cache_path,
            min_export_score=args.min_export_score,
        )
    except Exception as exc:
        print(f"[ERROR] network discovery failed: {exc}", file=sys.stderr)
        sys.exit(1)

    accepted = int(
        (result.edges_df["status"].astype(str).str.startswith("accepted")).sum()
        if not result.edges_df.empty
        else 0
    )
    print("[OK] Custom network discovery complete.")
    print(f"snapshot_id: {result.snapshot_id}")
    print(f"edges_discovered: {len(result.edges_df)}")
    print(f"accepted_edges: {accepted}")
    print(f"db_path: {result.db_path}")
    print(f"export_path: {result.export_path}")


if __name__ == "__main__":
    main()

