"""
Command-line interface for MethylEnricher
"""

import argparse
import json
import sys
from pathlib import Path

from .enricher import (
    run_enrichment,
    DEFAULT_LIBRARIES,
    LIBRARY_PRESETS,
    resolve_enrichr_libraries,
)
from .module_pipeline import run_cisbp_only, run_module_pipeline, run_ppi_hubs_only
from .queue_cli import QUEUE_SUBCOMMANDS, main_queue


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="MethylEnricher - Gene enrichment analysis for methylation DMPs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # MethylMapper combined CSV: focus on disease-associated genes only
  methyl_enricher --input /path/to/all-gene_name-combined.csv --disease-only --top 200 --outdir results

  # PCa-focused: disease-associated, direct/indirect, minimum evidence and score
  methyl_enricher --input all-gene_name-combined.csv --gene-column gene_name \\
    --disease-only --disease-association-type direct indirect \\
    --min-disease-evidence-level medium --min-disease-score 0.2 \\
    --min-dmp-count 2 --max-gene-q-value 0.05 --top 150 --outdir enricher_pca

  # Sort by gene_importance before selecting top genes
  methyl_enricher --input all-gene_name-combined.csv --gene-column gene_name \\
    --sort-by gene_importance --top 200 --outdir results

  # Use a config file (CLI overrides config)
  methyl_enricher --config enricher_config.json
  methyl_enricher --config enricher_config.json --top 100 --outdir other_dir

For theory and package documentation, see:
  packages/methylenricher/docs/ and docs/theory/
        """
    )
    
    # Input/Output arguments
    io_group = parser.add_argument_group('Input/Output')
    io_group.add_argument(
        '--config', '-C',
        type=str,
        default=None,
        metavar='JSON',
        help='Load options from JSON config file (CLI overrides config)'
    )
    io_group.add_argument(
        '--input', '-i',
        type=str,
        default=None,
        help='Input file with gene symbols (or set "input" in --config)'
    )
    io_group.add_argument(
        '--gene-column',
        type=str,
        default=None,
        help='Gene column name when input is CSV/TSV (default: auto-detect)'
    )
    io_group.add_argument(
        '--disease-only',
        action='store_true',
        help='When input is MethylMapper CSV, keep only disease_associated == True'
    )
    io_group.add_argument(
        '--disease-association-type',
        type=str,
        nargs='+',
        default=None,
        metavar='TYPE',
        help='Keep only these association types (e.g. direct indirect). Default: all'
    )
    io_group.add_argument(
        '--min-disease-evidence-level',
        type=str,
        choices=['low', 'medium', 'high'],
        default=None,
        help='Minimum disease_evidence_level (high = strictest)'
    )
    io_group.add_argument(
        '--min-disease-publications',
        type=int,
        default=None,
        metavar='N',
        help='Minimum disease_publications count'
    )
    io_group.add_argument(
        '--min-disease-score',
        type=float,
        default=None,
        metavar='S',
        help='Minimum disease_score (Open Targets)'
    )
    io_group.add_argument(
        '--min-dmp-count',
        type=int,
        default=None,
        metavar='N',
        help='Minimum dmp_count per gene'
    )
    io_group.add_argument(
        '--min-unique-dmps',
        type=int,
        default=None,
        metavar='N',
        help='Minimum unique_dmps per gene'
    )
    io_group.add_argument(
        '--max-gene-q-value',
        type=float,
        default=None,
        metavar='Q',
        help='Maximum gene_q_value (keep more significant genes)'
    )
    io_group.add_argument(
        '--min-gene-z',
        type=float,
        default=None,
        metavar='Z',
        help='Minimum |gene_z| (effect strength)'
    )
    io_group.add_argument(
        '--min-gene-importance',
        type=float,
        default=None,
        metavar='I',
        help='Minimum gene_importance (requires mapper gene_importance column)'
    )
    io_group.add_argument(
        '--sort-by',
        type=str,
        default=None,
        help='Column to sort by when input is CSV/TSV (default: auto gene_importance)'
    )
    io_group.add_argument(
        '--sort-ascending',
        action='store_true',
        help='Sort ascending (default: descending)'
    )
    io_group.add_argument(
        '--outdir', '-o',
        type=str,
        default='results',
        help='Output directory for results (default: results)'
    )
    io_group.add_argument(
        '--project', '-p',
        type=str,
        default=None,
        metavar='JSON',
        help='Path to pipeline project config; sets input to mapper combined CSV and output to enricher dir'
    )
    io_group.add_argument(
        '--step-override',
        type=str,
        default=None,
        metavar='JSON',
        help='Optional JSON overrides for enricher step when using --project (e.g. input, output_dir)'
    )
    from methyl_utils.cli_resolved_config import add_resolved_config_argument

    add_resolved_config_argument(io_group, help_suffix="(enricher actionConfig slice)")
    
    # Enrichment parameters
    enrich_group = parser.add_argument_group('Enrichment Parameters')
    enrich_group.add_argument(
        '--libraries', '-l',
        nargs='+',
        default=None,
        help=f'Enrichr libraries to query (default: {len(DEFAULT_LIBRARIES)} standard libraries)'
    )
    enrich_group.add_argument(
        '--library-preset',
        type=str,
        choices=sorted(LIBRARY_PRESETS.keys()),
        default=None,
        help='Named Enrichr preset (used when --libraries is not provided)'
    )
    enrich_group.add_argument(
        '--cisbp',
        action='store_true',
        default=False,
        help='Enable the CIS-BP TF-motif integration (also configurable via step_config.enricher.cisbp)'
    )
    enrich_group.add_argument(
        '--cisbp-mode',
        type=str,
        choices=['gene_sets', 'annotate', 'motif_scan'],
        default=None,
        help="CIS-BP mode: gene_sets (default), annotate (TF metadata), or motif_scan (DMP regions)."
    )
    enrich_group.add_argument(
        '--top', '-t',
        type=int,
        default=200,
        help='Use only top N genes from the input list (default: 200)'
    )
    enrich_group.add_argument(
        '--cutoff', '-c',
        type=float,
        default=0.05,
        help='Adjusted p-value cutoff for filtering significant results (default: 0.05)'
    )
    enrich_group.add_argument(
        '--organism',
        type=str,
        default='Human',
        help='Organism for Enrichr analysis (default: Human)'
    )
    enrich_group.add_argument(
        '--string-species',
        type=int,
        default=9606,
        dest='string_species',
        help='NCBI taxon id for STRING PPI network refinement (default: 9606 human; '
             '3702 Arabidopsis, 3847 soybean).'
    )
    
    # Module pipeline (pathway-to-module)
    parser.add_argument(
        '--modules', '-m',
        action='store_true',
        help='Run pathway-to-module pipeline: cluster pathways into modules, score and rank, write modules_ranked.csv'
    )
    parser.add_argument(
        '--ppi-only',
        action='store_true',
        help='PPI-only: skip Enrichr libraries and pathway modules; build ppi_hubs.csv directly '
             'from gene_importance-ranked mapper genes (fast; score-comparable across runs).'
    )
    parser.add_argument(
        '--cisbp-only',
        action='store_true',
        help='CIS-BP-only: skip Enrichr libraries, PPI and modules; run only the configured '
             'CIS-BP mode(s) on gene_importance-ranked mapper genes (requires --cisbp config).'
    )
    parser.add_argument(
        '--similarity-threshold',
        type=float,
        default=0.15,
        metavar='F',
        help='Pathway similarity threshold for clustering (default: 0.15). Lower values yield more edges and fewer, larger modules.'
    )
    parser.add_argument(
        '--cluster-resolution',
        type=float,
        default=0.8,
        metavar='F',
        help='Louvain cluster resolution (default: 0.8). Lower values yield fewer, larger modules. Tune with --similarity-threshold to target 3-5 modules.'
    )
    parser.add_argument(
        '--cluster-seed',
        type=int,
        default=42,
        metavar='N',
        help='Random seed for Louvain clustering reproducibility (default: 42).'
    )
    parser.add_argument(
        '--module-cluster-max-q',
        type=float,
        default=None,
        metavar='Q',
        help='When --modules: keep only terms with Adjusted P-value <= Q before clustering (default: disabled).'
    )
    parser.add_argument(
        '--module-cluster-top-terms-per-library',
        type=int,
        default=None,
        metavar='N',
        help='When --modules: keep top N terms per library before clustering (default: disabled).'
    )
    parser.add_argument(
        '--module-label-mode',
        type=str,
        choices=['canonical_only', 'dual_label'],
        default='dual_label',
        help='When --modules: module label policy. canonical_only uses canonical library evidence only; dual_label appends perturbation support as subtitle.'
    )
    parser.add_argument(
        '--network-plot',
        type=str,
        default=None,
        choices=['none', 'plotly', 'pyvis', 'cytoscape', 'dash', 'all'],
        metavar='MODE',
        help='When --modules: generate network plot. none=skip; plotly=Plotly HTML (default when -m); pyvis=PyVis HTML; cytoscape=Cytoscape.js HTML+JSON; dash=interactive Dash Cytoscape server; all=plotly+pyvis+cytoscape.'
    )
    parser.add_argument(
        '--dash-host',
        type=str,
        default='127.0.0.1',
        metavar='HOST',
        help='When --network-plot dash: host to bind Dash server (default: 127.0.0.1).'
    )
    parser.add_argument(
        '--dash-port',
        type=int,
        default=8050,
        metavar='PORT',
        help='When --network-plot dash: port for Dash server (default: 8050).'
    )
    parser.add_argument(
        '--dash-open-browser',
        action='store_true',
        help='When --network-plot dash: open browser automatically.'
    )
    parser.add_argument(
        '--network-refinement-enabled',
        action='store_true',
        help='When --modules: enable optional STRING-based PPI network refinement stage.'
    )
    parser.add_argument(
        '--network-refinement-source',
        type=str,
        choices=['string_api', 'local_edges'],
        default='string_api',
        metavar='SRC',
        help='Network refinement edge source (default: string_api).'
    )
    parser.add_argument(
        '--network-refinement-local-edges-file',
        type=str,
        default=None,
        metavar='CSV',
        help='Local CSV edge list file for network refinement when source=local_edges.'
    )
    parser.add_argument(
        '--network-refinement-cache-path',
        type=str,
        default=None,
        metavar='PATH',
        help='Optional shared cache path for STRING API edges (directory or .csv file path).'
    )
    parser.add_argument(
        '--network-refinement-score-threshold',
        type=float,
        default=400.0,
        metavar='S',
        help='STRING interaction score threshold in [0,1000] (default: 400).'
    )
    parser.add_argument(
        '--network-refinement-community-method',
        type=str,
        choices=['louvain', 'label_propagation', 'connected_components'],
        default='louvain',
        metavar='M',
        help='Community detection method for PPI graph (default: louvain).'
    )
    parser.add_argument(
        '--network-refinement-min-component-size',
        type=int,
        default=2,
        metavar='N',
        help='Minimum connected component size to keep in PPI graph (default: 2).'
    )
    parser.add_argument(
        '--network-refinement-weight-in-final-score',
        type=float,
        default=0.3,
        metavar='W',
        help='Blend weight for ppi_coherence_score in [0,1] (default: 0.3).'
    )
    parser.add_argument(
        '--network-refinement-hub-ranking-mode',
        type=str,
        choices=['signal_weighted', 'topology'],
        default='signal_weighted',
        metavar='MODE',
        help='Hub ranking: signal_weighted combines PPI centrality with methylation gene weights '
        '(default); topology uses graph centrality only.',
    )
    parser.add_argument(
        '--network-refinement-hub-disease-boost',
        type=float,
        default=0.0,
        metavar='B',
        help='When disease prior genes exist: multiply combined hub score by (1+B) for those genes (default: 0).',
    )
    parser.add_argument(
        '--network-refinement-hub-w-degree',
        type=float,
        default=None,
        metavar='W',
        help='Weight for normalized degree centrality in topology_score (default: 1/3 if all three unset).',
    )
    parser.add_argument(
        '--network-refinement-hub-w-betweenness',
        type=float,
        default=None,
        metavar='W',
        help='Weight for normalized betweenness in topology_score (default: 1/3 if all three unset).',
    )
    parser.add_argument(
        '--network-refinement-hub-w-closeness',
        type=float,
        default=None,
        metavar='W',
        help='Weight for normalized closeness in topology_score (default: 1/3 if all three unset).',
    )

    # Other options
    parser.add_argument(
        '--list-libraries',
        action='store_true',
        help='List all available Enrichr libraries and exit'
    )
    parser.add_argument(
        '--version',
        action='version',
        version='MethylEnricher 0.1.0'
    )

    complete_group = parser.add_argument_group('Ensure-complete mode')
    complete_group.add_argument(
        '--ensure-complete',
        action='store_true',
        help='Retry failed libraries until all are present; exit 1 if any comparison incomplete',
    )
    complete_group.add_argument(
        '--verify-only',
        action='store_true',
        help='With --ensure-complete: check artifacts only (no Enrichr API calls)',
    )
    complete_group.add_argument(
        '--comparison',
        type=str,
        default=None,
        metavar='LABEL',
        help='Run or verify only this comparison label (e.g. PCa_PCa3)',
    )
    complete_group.add_argument(
        '--force',
        action='store_true',
        help='Re-query Enrichr even when per-library CSV exists',
    )
    complete_group.add_argument(
        '--no-retry',
        action='store_true',
        help='Disable retry/backoff on Enrichr failures (legacy fast-fail per library)',
    )

    # Apply config file before parsing so CLI overrides config
    argv = sys.argv[1:]
    config_path = None
    for i, a in enumerate(argv):
        if a in ("--config", "-C") and i + 1 < len(argv):
            config_path = Path(argv[i + 1])
            break
    if config_path and config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        ns = argparse.Namespace()
        for action in parser._actions:
            if getattr(action, "dest", None) and action.dest not in ("help",) and not action.dest.startswith("_"):
                ns.__setattr__(action.dest, action.default)
        for key, value in cfg.items():
            attr = key.replace("-", "_")
            if hasattr(ns, attr):
                ns.__setattr__(attr, value)
        return parser.parse_args(namespace=ns)
    return parser.parse_args()


def _apply_enricher_config_to_args(args, config: "EnricherStepConfig") -> None:
    """Apply EnricherStepConfig to parsed args. CLI overrides config when user explicitly passes a value."""
    from .config import EnricherStepConfig
    # I/O: only when args not set (CLI overrides)
    if config.input_file is not None and args.input is None:
        args.input = config.input_file
    if config.input is not None and args.input is None:
        args.input = config.input
    if config.output_dir is not None and args.outdir in (None, "results"):
        args.outdir = config.output_dir
    if config.outdir is not None and args.outdir in (None, "results"):
        args.outdir = config.outdir
    def _apply_network_refinement_field(attr: str, value, default):
        if value is None:
            return
        if not hasattr(args, attr):
            return
        if getattr(args, attr) == default:
            setattr(args, attr, value)

    # Support nested network_refinement object while preserving flat-key compatibility.
    if config.network_refinement is not None:
        nr = config.network_refinement
        _apply_network_refinement_field("network_refinement_enabled", nr.enabled, False)
        _apply_network_refinement_field("network_refinement_source", nr.source, "string_api")
        _apply_network_refinement_field("network_refinement_local_edges_file", nr.local_edges_file, None)
        _apply_network_refinement_field("network_refinement_cache_path", nr.cache_path, None)
        _apply_network_refinement_field("network_refinement_score_threshold", nr.score_threshold, 400.0)
        _apply_network_refinement_field("network_refinement_community_method", nr.community_method, "louvain")
        _apply_network_refinement_field("network_refinement_min_component_size", nr.min_component_size, 2)
        _apply_network_refinement_field("network_refinement_weight_in_final_score", nr.weight_in_final_score, 0.3)
        _apply_network_refinement_field("network_refinement_hub_ranking_mode", nr.hub_ranking_mode, "signal_weighted")
        _apply_network_refinement_field("network_refinement_hub_disease_boost", nr.hub_disease_boost, 0.0)
        _apply_network_refinement_field("network_refinement_hub_w_degree", nr.hub_w_degree, None)
        _apply_network_refinement_field("network_refinement_hub_w_betweenness", nr.hub_w_betweenness, None)
        _apply_network_refinement_field("network_refinement_hub_w_closeness", nr.hub_w_closeness, None)

    # Flat-key compatibility (legacy or simple configs).
    _apply_network_refinement_field(
        "network_refinement_enabled",
        config.network_refinement_enabled,
        False,
    )
    _apply_network_refinement_field(
        "network_refinement_source",
        config.network_refinement_source,
        "string_api",
    )
    _apply_network_refinement_field(
        "network_refinement_local_edges_file",
        config.network_refinement_local_edges_file,
        None,
    )
    _apply_network_refinement_field(
        "network_refinement_cache_path",
        config.network_refinement_cache_path,
        None,
    )
    _apply_network_refinement_field(
        "network_refinement_score_threshold",
        config.network_refinement_score_threshold,
        400.0,
    )
    _apply_network_refinement_field(
        "network_refinement_community_method",
        config.network_refinement_community_method,
        "louvain",
    )
    _apply_network_refinement_field(
        "network_refinement_min_component_size",
        config.network_refinement_min_component_size,
        2,
    )
    _apply_network_refinement_field(
        "network_refinement_weight_in_final_score",
        config.network_refinement_weight_in_final_score,
        0.3,
    )
    _apply_network_refinement_field(
        "network_refinement_hub_ranking_mode",
        config.network_refinement_hub_ranking_mode,
        "signal_weighted",
    )
    _apply_network_refinement_field(
        "network_refinement_hub_disease_boost",
        config.network_refinement_hub_disease_boost,
        0.0,
    )
    _apply_network_refinement_field(
        "network_refinement_hub_w_degree",
        config.network_refinement_hub_w_degree,
        None,
    )
    _apply_network_refinement_field(
        "network_refinement_hub_w_betweenness",
        config.network_refinement_hub_w_betweenness,
        None,
    )
    _apply_network_refinement_field(
        "network_refinement_hub_w_closeness",
        config.network_refinement_hub_w_closeness,
        None,
    )

    # CIS-BP TF-motif integration: assemble a CisbpConfig from the nested object
    # plus the flat quick-toggles, and stash it on args (it is not an argparse arg).
    from .config import CisbpConfig

    cisbp_cfg = config.cisbp
    if cisbp_cfg is None and (config.cisbp_enabled is not None or config.cisbp_mode is not None):
        cisbp_cfg = CisbpConfig()
    if cisbp_cfg is not None:
        updates = {}
        if config.cisbp_enabled is not None:
            updates["enabled"] = config.cisbp_enabled
        if config.cisbp_mode is not None:
            updates["mode"] = config.cisbp_mode
        if updates:
            cisbp_cfg = cisbp_cfg.model_copy(update=updates)
    if getattr(args, "cisbp_config", None) is None:
        args.cisbp_config = cisbp_cfg

    # Rest: set from config. For network_plot, only set when user did not pass --network-plot (args is None).
    config_values = config.model_dump(mode="python", exclude_none=True)
    for name in EnricherStepConfig.model_fields:
        if name in (
            "input",
            "input_file",
            "output_dir",
            "outdir",
            "cisbp",
            "cisbp_enabled",
            "cisbp_mode",
            "network_refinement",
            "network_refinement_enabled",
            "network_refinement_source",
            "network_refinement_local_edges_file",
            "network_refinement_cache_path",
            "network_refinement_score_threshold",
            "network_refinement_community_method",
            "network_refinement_min_component_size",
            "network_refinement_weight_in_final_score",
            "network_refinement_hub_ranking_mode",
            "network_refinement_hub_disease_boost",
            "network_refinement_hub_w_degree",
            "network_refinement_hub_w_betweenness",
            "network_refinement_hub_w_closeness",
        ):
            continue
        if name not in config_values:
            continue
        val = config_values[name]
        if not hasattr(args, name):
            continue
        if name == "network_plot" and getattr(args, name) is not None:
            # CLI --network-plot wins over config
            continue
        setattr(args, name, val)


def _resolve_cisbp_config_with_cli(args):
    """Combine config-derived CisbpConfig with CLI --cisbp/--cisbp-mode overrides."""
    cisbp_config = getattr(args, "cisbp_config", None)
    # --cisbp-only implies running CIS-BP, so treat it as enabling the integration.
    cli_enable = bool(getattr(args, "cisbp", False)) or bool(getattr(args, "cisbp_only", False))
    cli_mode = getattr(args, "cisbp_mode", None)
    if not cli_enable and not cli_mode:
        return cisbp_config
    from .config import CisbpConfig

    if cisbp_config is None:
        cisbp_config = CisbpConfig()
    updates = {}
    if cli_enable:
        updates["enabled"] = True
    if cli_mode:
        updates["mode"] = cli_mode
    return cisbp_config.model_copy(update=updates)


def _build_cisbp_context(args, cisbp_config, project, project_path):
    """Resolve cache dir, GTF, genome FASTA and gene universe for CIS-BP."""
    from .cisbp import resolve_cisbp_context

    return resolve_cisbp_context(
        cisbp_config,
        project=project,
        project_path=project_path,
        cutoff=getattr(args, "cutoff", 0.05),
    )


def list_available_libraries():
    """List all available Enrichr libraries."""
    try:
        import gseapy as gp
    except ImportError:
        print("[ERROR] gseapy is required. Install with: pip install gseapy")
        sys.exit(1)
    
    print("Available Enrichr libraries:")
    print("=" * 70)
    
    try:
        libraries = gp.get_library_name(organism='Human')
        for i, lib in enumerate(libraries, 1):
            print(f"{i:3d}. {lib}")
        print("=" * 70)
        print(f"Total: {len(libraries)} libraries available")
    except Exception as e:
        print(f"[ERROR] Could not retrieve library list: {e}")
        sys.exit(1)


def main():
    """Main entry point for MethylEnricher CLI."""
    if len(sys.argv) > 1 and sys.argv[1] in QUEUE_SUBCOMMANDS:
        sys.exit(main_queue(sys.argv[1:]))

    args = parse_args()
    if not hasattr(args, "cisbp_config"):
        args.cisbp_config = None

    project = None
    project_path = None

    # Resolve paths and apply step_config from --project if set
    if args.project:
        from methyl_utils import load_project
        from .project_resolver import resolve_enricher_paths, resolve_enricher_paths_per_cancer_group
        from .config import EnricherStepConfig
        project_path = Path(args.project)
        if not project_path.exists() and not project_path.is_absolute():
            # When run from a package dir (e.g. packages/methylenricher), try repo root
            _repo_root = Path(__file__).resolve().parent.parent.parent.parent
            _alt = (_repo_root / args.project).resolve()
            if _alt.exists():
                project_path = _alt
        if not project_path.exists():
            print(f"[ERROR] Project config not found: {project_path}")
            sys.exit(1)
        project = load_project(project_path)
        from methyl_utils.cli_resolved_config import resolve_cli_step_config

        step_cfg = resolve_cli_step_config(
            "enricher",
            project,
            resolved_config_path=getattr(args, "resolved_config", None),
            step_override_path=args.step_override,
        )
        if step_cfg:
            enricher_config = EnricherStepConfig.model_validate(step_cfg)
            _apply_enricher_config_to_args(args, enricher_config)
        else:
            enricher_config = None
        step_override = Path(args.step_override) if args.step_override else None
        use_ensure = bool(getattr(args, "ensure_complete", False)) or bool(
            enricher_config and enricher_config.ensure_complete
        )
        if use_ensure:
            from .ensure_complete import run_project_ensure_complete

            if enricher_config and enricher_config.modules and not getattr(args, "modules", False):
                args.modules = True
            all_ok, _ = run_project_ensure_complete(
                project_path,
                comparison=getattr(args, "comparison", None),
                force=bool(getattr(args, "force", False)),
                verify_only=bool(getattr(args, "verify_only", False)),
                step_override_path=step_override,
                run_kwargs={"modules": bool(getattr(args, "modules", False))},
            )
            sys.exit(0 if all_ok else 1)
        # Use per-comparison layout (enricher/<control>/<disease> per comparison) when project has multiple groups
        per_group = resolve_enricher_paths_per_cancer_group(project_path, step_override)
        if per_group and args.input is None and (args.outdir == "results" or args.outdir is None):
            args.enricher_per_group = per_group
            args.input = None
            args.outdir = None
        elif per_group and (args.input is not None or (args.outdir != "results" and args.outdir is not None)):
            # Explicit --input or --outdir: single run, clear per-group
            args.enricher_per_group = None
            paths = resolve_enricher_paths(project_path, step_override)
            if not args.input:
                args.input = paths.input_file
            if args.outdir == "results" or args.outdir is None:
                args.outdir = paths.output_dir
        else:
            args.enricher_per_group = None
            paths = resolve_enricher_paths(project_path, step_override)
            if not args.input:
                args.input = paths.input_file
            if args.outdir == "results":  # default only
                args.outdir = paths.output_dir
    
    # Handle --list-libraries
    if args.list_libraries:
        list_available_libraries()
        sys.exit(0)

    per_group = getattr(args, "enricher_per_group", None)
    if not per_group and not args.input:
        print("[ERROR] Input file not specified. Use --input /path/to/file or set 'input' in --config.")
        sys.exit(1)

    # Resolve final library list once with explicit precedence:
    # --libraries > --library-preset > defaults.
    resolved_libraries = resolve_enrichr_libraries(
        libraries=args.libraries,
        library_preset=args.library_preset
    )

    # Display parameters
    print("=" * 70)
    print("MethylEnricher - Gene Enrichment Analysis")
    print("=" * 70)
    if per_group:
        print(f"Per comparison: {len(per_group)} run(s)")
        input_path = None
    else:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"[ERROR] Input file not found: {input_path}")
            sys.exit(1)
        print(f"Input file: {input_path}")
        print(f"Output directory: {args.outdir}")
    print(f"Top genes: {args.top}")
    print(f"Cutoff: q ≤ {args.cutoff}")
    print(f"Organism: {args.organism}")
    if args.gene_column:
        print(f"Gene column: {args.gene_column}")
    if args.sort_by:
        print(f"Sort by: {args.sort_by} ({'asc' if args.sort_ascending else 'desc'})")
    if args.disease_only:
        print("Filter: disease_associated = True")
    if args.disease_association_type:
        print(f"Filter: disease_association_type in {args.disease_association_type}")
    if args.min_disease_evidence_level:
        print(f"Filter: disease_evidence_level >= {args.min_disease_evidence_level}")
    if args.min_disease_publications is not None:
        print(f"Filter: disease_publications >= {args.min_disease_publications}")
    if args.min_disease_score is not None:
        print(f"Filter: disease_score >= {args.min_disease_score}")
    if args.min_dmp_count is not None:
        print(f"Filter: dmp_count >= {args.min_dmp_count}")
    if args.max_gene_q_value is not None:
        print(f"Filter: gene_q_value <= {args.max_gene_q_value}")
    if args.min_gene_importance is not None:
        print(f"Filter: gene_importance >= {args.min_gene_importance}")
    
    if args.libraries:
        print("Libraries source: explicit --libraries")
        print(f"Libraries: {', '.join(resolved_libraries)}")
    elif args.library_preset:
        print(f"Libraries source: preset '{args.library_preset}'")
        print(f"Libraries: {', '.join(resolved_libraries)}")
    else:
        print(f"Libraries source: default ({len(DEFAULT_LIBRARIES)} libraries)")
        print(f"Libraries: {', '.join(resolved_libraries)}")
    if getattr(args, "modules", False):
        print("Mode: pathway-to-module pipeline (output: modules_ranked.csv)")
        print(f"Similarity threshold: {getattr(args, 'similarity_threshold', 0.15)}")
        print(f"Cluster resolution: {getattr(args, 'cluster_resolution', 0.8)}")
        print(f"Cluster seed: {getattr(args, 'cluster_seed', 42)}")
        if getattr(args, "module_cluster_max_q", None) is not None:
            print(f"Module term filter: Adjusted P-value <= {args.module_cluster_max_q}")
        if getattr(args, "module_cluster_top_terms_per_library", None) is not None:
            print(
                "Module term filter: "
                f"top {args.module_cluster_top_terms_per_library} terms/library"
            )
        print(f"Module label mode: {getattr(args, 'module_label_mode', 'dual_label')}")
        _np = getattr(args, "network_plot", None)
        effective_network_plot = "plotly" if _np is None else _np
        if effective_network_plot and effective_network_plot.lower() != "none":
            print(f"Network plot: {effective_network_plot}")
        if effective_network_plot and effective_network_plot.lower() == "dash":
            print(f"Dash host: {args.dash_host}")
            print(f"Dash port: {args.dash_port}")
            print(f"Dash open browser: {bool(args.dash_open_browser)}")
        if getattr(args, "network_refinement_enabled", False):
            print("Network refinement: enabled")
            print(f"  source={args.network_refinement_source}")
            if args.network_refinement_local_edges_file:
                print(f"  local_edges_file={args.network_refinement_local_edges_file}")
            if args.network_refinement_cache_path:
                print(f"  cache_path={args.network_refinement_cache_path}")
            print(f"  score_threshold={args.network_refinement_score_threshold}")
            print(f"  community_method={args.network_refinement_community_method}")
            print(f"  min_component_size={args.network_refinement_min_component_size}")
            print(f"  weight_in_final_score={args.network_refinement_weight_in_final_score}")
            print(f"  hub_ranking_mode={getattr(args, 'network_refinement_hub_ranking_mode', 'signal_weighted')}")
            print(f"  hub_disease_boost={getattr(args, 'network_refinement_hub_disease_boost', 0.0)}")
    print("=" * 70)
    
    cisbp_config = _resolve_cisbp_config_with_cli(args)
    cisbp_context = None
    if cisbp_config is not None and getattr(cisbp_config, "enabled", False):
        cisbp_context = _build_cisbp_context(args, cisbp_config, project, project_path)
        print(
            f"CIS-BP: enabled (mode={cisbp_config.mode}, source={cisbp_config.gene_set_source}, "
            f"species={cisbp_config.species})"
        )

    def _run_one(in_file: Path, out_dir: str):
        if getattr(args, "cisbp_only", False):
            return run_cisbp_only(
                input_path=in_file,
                output_dir=Path(out_dir),
                cisbp=cisbp_config,
                cisbp_context=cisbp_context,
                organism=args.organism,
                gene_column=args.gene_column,
                top_n=args.top,
                disease_only=args.disease_only,
                disease_association_types=args.disease_association_type,
                min_disease_evidence_level=args.min_disease_evidence_level,
                min_disease_publications=args.min_disease_publications,
                min_disease_score=args.min_disease_score,
                min_dmp_count=args.min_dmp_count,
                min_unique_dmps=args.min_unique_dmps,
                max_gene_q_value=args.max_gene_q_value,
                min_gene_z=args.min_gene_z,
                min_gene_importance=args.min_gene_importance,
                sort_by=args.sort_by,
                sort_ascending=args.sort_ascending,
            )
        if getattr(args, "ppi_only", False):
            return run_ppi_hubs_only(
                input_path=in_file,
                output_dir=Path(out_dir),
                organism=args.organism,
                gene_column=args.gene_column,
                top_n=args.top,
                disease_only=args.disease_only,
                disease_association_types=args.disease_association_type,
                min_disease_evidence_level=args.min_disease_evidence_level,
                min_disease_publications=args.min_disease_publications,
                min_disease_score=args.min_disease_score,
                min_dmp_count=args.min_dmp_count,
                min_unique_dmps=args.min_unique_dmps,
                max_gene_q_value=args.max_gene_q_value,
                min_gene_z=args.min_gene_z,
                min_gene_importance=args.min_gene_importance,
                sort_by=args.sort_by,
                sort_ascending=args.sort_ascending,
                network_refinement_source=getattr(args, "network_refinement_source", "string_api"),
                network_refinement_local_edges_file=getattr(args, "network_refinement_local_edges_file", None),
                network_refinement_cache_path=getattr(args, "network_refinement_cache_path", None),
                network_refinement_score_threshold=getattr(args, "network_refinement_score_threshold", 400.0),
                network_refinement_community_method=getattr(args, "network_refinement_community_method", "louvain"),
                network_refinement_min_component_size=getattr(args, "network_refinement_min_component_size", 2),
                network_refinement_hub_ranking_mode=getattr(args, "network_refinement_hub_ranking_mode", "signal_weighted"),
                network_refinement_hub_disease_boost=getattr(args, "network_refinement_hub_disease_boost", 0.0),
                network_refinement_hub_w_degree=getattr(args, "network_refinement_hub_w_degree", None),
                network_refinement_hub_w_betweenness=getattr(args, "network_refinement_hub_w_betweenness", None),
                network_refinement_hub_w_closeness=getattr(args, "network_refinement_hub_w_closeness", None),
                network_refinement_species=getattr(args, "string_species", 9606),
            )
        if getattr(args, "modules", False):
            _np = getattr(args, "network_plot", None)
            effective_network_plot = "plotly" if _np is None else _np
            return run_module_pipeline(
                input_path=in_file,
                output_dir=Path(out_dir),
                gene_column=args.gene_column,
                top_n=args.top,
                libraries=resolved_libraries,
                organism=args.organism,
                cutoff=args.cutoff,
                disease_only=args.disease_only,
                disease_association_types=args.disease_association_type,
                min_disease_evidence_level=args.min_disease_evidence_level,
                min_disease_publications=args.min_disease_publications,
                min_disease_score=args.min_disease_score,
                min_dmp_count=args.min_dmp_count,
                min_unique_dmps=args.min_unique_dmps,
                max_gene_q_value=args.max_gene_q_value,
                min_gene_z=args.min_gene_z,
                min_gene_importance=args.min_gene_importance,
                sort_by=args.sort_by,
                sort_ascending=args.sort_ascending,
                similarity_threshold=getattr(args, "similarity_threshold", 0.15),
                cluster_resolution=getattr(args, "cluster_resolution", 0.8),
                cluster_seed=getattr(args, "cluster_seed", 42),
                module_cluster_max_q=getattr(args, "module_cluster_max_q", None),
                module_cluster_top_terms_per_library=getattr(
                    args, "module_cluster_top_terms_per_library", None
                ),
                module_label_mode=getattr(args, "module_label_mode", "dual_label"),
                network_plot=effective_network_plot,
                network_refinement_enabled=getattr(args, "network_refinement_enabled", False),
                network_refinement_source=getattr(args, "network_refinement_source", "string_api"),
                network_refinement_local_edges_file=getattr(args, "network_refinement_local_edges_file", None),
                network_refinement_cache_path=getattr(args, "network_refinement_cache_path", None),
                network_refinement_score_threshold=getattr(args, "network_refinement_score_threshold", 400.0),
                network_refinement_community_method=getattr(args, "network_refinement_community_method", "louvain"),
                network_refinement_min_component_size=getattr(args, "network_refinement_min_component_size", 2),
                network_refinement_weight_in_final_score=getattr(args, "network_refinement_weight_in_final_score", 0.3),
                network_refinement_hub_ranking_mode=getattr(
                    args, "network_refinement_hub_ranking_mode", "signal_weighted"
                ),
                network_refinement_hub_disease_boost=getattr(
                    args, "network_refinement_hub_disease_boost", 0.0
                ),
                network_refinement_hub_w_degree=getattr(args, "network_refinement_hub_w_degree", None),
                network_refinement_hub_w_betweenness=getattr(
                    args, "network_refinement_hub_w_betweenness", None
                ),
                network_refinement_hub_w_closeness=getattr(args, "network_refinement_hub_w_closeness", None),
                network_refinement_species=getattr(args, "string_species", 9606),
                dash_host=getattr(args, "dash_host", "127.0.0.1"),
                dash_port=getattr(args, "dash_port", 8050),
                dash_open_browser=getattr(args, "dash_open_browser", False),
                cisbp=cisbp_config,
                cisbp_context=cisbp_context,
            )
        return run_enrichment(
            input_file=in_file,
            output_dir=out_dir,
            libraries=resolved_libraries,
            top_n=args.top,
            cutoff=args.cutoff,
            organism=args.organism,
            gene_column=args.gene_column,
            disease_only=args.disease_only,
            disease_association_types=args.disease_association_type,
            min_disease_evidence_level=args.min_disease_evidence_level,
            min_disease_publications=args.min_disease_publications,
            min_disease_score=args.min_disease_score,
            min_dmp_count=args.min_dmp_count,
            min_unique_dmps=args.min_unique_dmps,
            max_gene_q_value=args.max_gene_q_value,
            min_gene_z=args.min_gene_z,
            min_gene_importance=args.min_gene_importance,
            sort_by=args.sort_by,
            sort_ascending=args.sort_ascending,
            cisbp=cisbp_config,
            cisbp_context=cisbp_context,
        )

    try:
        if per_group:
            # Run enrichment once per comparison (input from mapper/<control>/<disease>, output to enricher/<control>/<disease>)
            for paths, label in per_group:
                inp = Path(paths.input_file)
                if not inp.exists():
                    print(f"[WARN] Skipping group {label}: input not found: {inp}")
                    continue
                print(f"\n--- Enrichment for group: {label} -> {paths.output_dir} ---")
                try:
                    results = _run_one(inp, paths.output_dir)
                except ValueError as e:
                    if "No genes left after filters" in str(e):
                        print(f"[WARN] Skipping group {label}: {e}")
                        continue
                    raise
                if results.empty:
                    print(f"[WARN] No enrichment results for {label}.")
                else:
                    print(f"[OK] {label}: results saved to {paths.output_dir}")
            msg = "Per-comparison module pipeline complete!" if getattr(args, "modules", False) else "Per-comparison enrichment complete!"
            print(f"\n[SUCCESS] {msg}")
            sys.exit(0)
        else:
            results = _run_one(input_path, args.outdir)
            if results.empty:
                print("\n[WARN] No enrichment results found. Check your gene list and try again.")
                sys.exit(1)
            if getattr(args, "modules", False):
                print("\n[SUCCESS] Pathway-to-module pipeline complete!")
                print(f"Results saved to: {args.outdir} (including modules_ranked.csv)")
            else:
                print("\n[SUCCESS] Enrichment analysis complete!")
                print(f"Results saved to: {args.outdir}")
            sys.exit(0)

    except KeyboardInterrupt:
        print("\n\n[WARN] Analysis interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n[ERROR] Analysis failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()

