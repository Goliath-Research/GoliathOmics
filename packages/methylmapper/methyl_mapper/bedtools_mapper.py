"""
Bedtools-based DMP-to-feature mapping for MethylMapper.

This module provides comprehensive mapping of DMPs to genomic features (genes, transcripts,
exons, introns, etc.) using bedtools intersect, with support for weighting by statistical
significance (p-values, q-values) and biological importance (effect_size).
"""

import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

try:
    from methyl_utils import storey_qvalues
except ImportError:
    from methyl_utils.statistical_tests import storey_qvalues

from methyl_utils.dmp_export_paths import glob_discovery_dmps_with_unified_fallback

from .gene_disease_enricher import GeneDiseaseEnricher
from .plant_trait_enricher import PlantTraitEnricher
from .gtf_regions import build_gene_bodies_bed, build_sp_regions_bed, parse_region_name
from .config import BiologyWeightConfig

logger = logging.getLogger(__name__)

class BedtoolsMapper:
    """
    Map DMPs to genomic features using bedtools intersect.

    Features:
    - By default intersects every GTF feature type; optional `feature_types` filter
    - Optional auxiliary BED overlaps and bedtools closest to nearest gene body
    - Weighting by p-value, q-value, and effect_size; gene-level Stouffer + Storey
    - Per-gene feature mix summaries when aggregating by gene_name / gene_id
    """
    _FEATURE_PRIORITY = {
        "promoter": 0,
        "exon": 1,
        "intron": 2,
        "gene_body": 3,
        "terminator": 4,
    }
    _FEATURE_HITS_COLS = {
        "promoter": "hits_promoter",
        "exon": "hits_exon",
        "intron": "hits_intron",
        "gene_body": "hits_gene_body",
        "terminator": "hits_terminator",
    }
    _FEATURE_SCORE_ORDER = ("promoter", "exon", "intron", "gene_body", "terminator")
    _PARENT_FEATURES = frozenset(_FEATURE_PRIORITY.keys())
    _DETAILED_TO_PARENT = {
        # Exonic-like detailed features
        "cds": "exon",
        "utr": "exon",
        "five_prime_utr": "exon",
        "three_prime_utr": "exon",
        "5utr": "exon",
        "3utr": "exon",
        "start_codon": "exon",
        "stop_codon": "exon",
        # Intron-like detailed features
        "retained_intron": "intron",
        # Container-like annotation rows
        "gene": "gene_body",
        "transcript": "gene_body",
        "mrna": "gene_body",
        "lncrna": "gene_body",
        "ncrna": "gene_body",
        "rrna": "gene_body",
        "snrna": "gene_body",
        "snorna": "gene_body",
        "pseudogene": "gene_body",
    }
    
    def __init__(
        self,
        gene_gtf: Path,
        feature_types: Optional[List[str]] = None,
        auxiliary_bed_paths: Optional[List[Path]] = None,
        run_bedtools_closest: bool = False,
        closest_gene_bed: Optional[Path] = None,
        use_p_value_weight: bool = True,
        use_q_value_weight: bool = True,
        use_effect_size_weight: bool = True,
        p_value_log_transform: bool = True,
        enrich_disease: bool = False,
        enrich_source: str = "grok+opentargets",
        separate_enrichment_sources: bool = False,
        disease_term: str = "early-stage prostate cancer",
        plant_traits_path: Optional[Path] = None,
        grok_api_key: Optional[str] = None,
        disgenet_api_key: Optional[str] = None,
        enrichment_profile: Optional[str] = None,
        min_evidence_level: Optional[str] = None,
        min_publications: Optional[int] = None,
        min_disgenet_score: Optional[float] = None,
        allow_predicted: Optional[bool] = None,
        cache_enabled: bool = True,
        cache_dir: Optional[Path] = None,  # Auto: project_root/enrich_cache or ./enrich_cache
        cache_backend: str = "sqlite",
        cache_db_path: Optional[Path] = None,
        cache_ttl_days: Optional[int] = 30,
        grok_cache_ttl_days: Optional[int] = 30,
        source_max_workers: int = 3,
        grok_batch_size: int = 16,
        grok_max_workers: int = 1,
        grok_use_xai_batch_api: bool = False,
        grok_batch_poll_interval: float = 2.0,
        grok_batch_submit_chunk_size: int = 200,
        grok_rate_limit_delay: float = 5.0,
        grok_max_retries: int = 6,
        grok_429_inter_batch_sleep: float = 180.0,
        open_targets_max_workers: int = 8,
        disgenet_max_workers: int = 8,
        azure_key_vault_url: Optional[str] = None,
        azure_secret_name: Optional[str] = None,
        encrypted_file_path: Optional[Path] = None,
        methyl_mapper_home: Optional[Path] = None,
        optimize_dmps: bool = True,
        dmp_rank_columns: Optional[List[str]] = None,
        min_k: int = 10,
        max_k: Optional[int] = None,
        stability_threshold: int = 3,
        unrelated_growth_threshold: float = 0.10,
        extend_after_stable: bool = True,
        use_sp_regions: bool = False,
        upstream_size: int = 5000,
        downstream_size: int = 2000,
        min_intron_size: int = 0,
        max_gap: int = 1,
        w_promoter: float = 2.0,
        w_terminator: float = 0.5,
        w_gene_body: float = 1.0,
        w_exon: float = 1.5,
        w_intron: float = 0.7,
        w_unknown: float = 1.0,
        biology_weights: Optional[BiologyWeightConfig] = None,
        storey_lambda: Optional[float] = None,
    ):
        """
        Initialize BedtoolsMapper.

        Args:
            gene_gtf: Path to GTF/GFF annotation file
            feature_types: GTF feature column values to keep (e.g. ['gene', 'exon']). If None or empty,
                          keep all feature types present in the GTF (default).
            auxiliary_bed_paths: Optional extra BED files (enhancers, ChIP peaks, etc.); overlaps merged into outputs.
            run_bedtools_closest: If True, run bedtools closest from each DMP to gene bodies (see closest_gene_bed).
            closest_gene_bed: BED of gene intervals for closest; if None and run_bedtools_closest, built from GTF gene rows.
            use_p_value_weight: Whether to weight by p-value
            use_q_value_weight: Whether to weight by q-value
            use_effect_size_weight: Whether to weight by effect_size
            p_value_log_transform: If True, uses -log10(p_value) for weighting
            enrich_disease: Whether to enrich results with disease associations
            enrich_source: Source(s) for disease enrichment ("grok", "opentargets", "grok+opentargets",
                          "disgenet", "both", "all", or "plant_traits") (default: "grok+opentargets")
            separate_enrichment_sources: If True, export separate files for each enrichment source
            disease_term: Disease term for enrichment (e.g., "early-stage prostate cancer")
            plant_traits_path: Offline gene↔trait TSV/CSV when enrich_source=plant_traits
            grok_api_key: Grok API key for disease enrichment (optional, uses secure storage if not provided)
            disgenet_api_key: DisGeNET API key for disease enrichment (optional, uses secure storage if not provided)
            enrichment_profile: Preset threshold profile (strict, balanced, permissive)
            min_evidence_level: Minimum evidence level to count as disease-associated
            min_publications: Minimum number of publications required
            min_disgenet_score: Minimum DisGeNET score required (0.0-1.0)
            allow_predicted: Whether to allow "predicted" associations
            cache_enabled: Whether to persist cache to disk
            cache_dir: Directory for disk cache (default: auto project_root/enrich_cache or ./enrich_cache)
            cache_backend: Cache persistence backend (default: sqlite)
            cache_db_path: SQLite cache DB path (default: <cache_dir>/gene_disease_cache.sqlite)
            cache_ttl_days: Disk cache TTL in days for Open Targets / DisGeNET lookups (default: 30)
            grok_cache_ttl_days: Disk cache TTL in days for Grok lookups (default: 30)
            source_max_workers: Max workers when querying multiple enrichment sources in parallel
            grok_batch_size: Number of genes per Grok batch request
            grok_max_workers: Max concurrent Grok requests (realtime API only)
            grok_use_xai_batch_api: Use xAI Batch API by default; set False for synchronous chat/completions
            grok_batch_poll_interval: Poll interval while waiting on xAI Batch API
            grok_batch_submit_chunk_size: Chunk size when POSTing batch requests to xAI
            open_targets_max_workers: Max concurrent Open Targets gene requests
            disgenet_max_workers: Max concurrent DisGeNET gene requests
            azure_key_vault_url: Azure Key Vault URL (or set AZURE_KEY_VAULT_URL env var)
            azure_secret_name: Optional Key Vault secret name override for enrichment keys (per-credential defaults if unset)
            encrypted_file_path: Path to encrypted credential file (optional)
            methyl_mapper_home: Root directory for default cache/ and credentials/ (default: ~/.methyl_mapper)
            optimize_dmps: Whether to optimize DMP count for stable gene sets (default: True)
            dmp_rank_columns: Optional list of columns to rank DMPs by importance
            min_k: Minimum number of DMPs to test (default: 10)
            max_k: Maximum number of DMPs to test (default: None, uses all available)
            stability_threshold: Number of consecutive iterations without new genes to consider stable (default: 3)
            unrelated_growth_threshold: Growth rate threshold for unrelated genes (legacy; not used in Phase 1)
            extend_after_stable: If True, after stabilization run optional extension loop when last gene is strongly disease-associated (default: True)
            use_sp_regions: If True, build SP-equivalent regions (promoter, terminator, gene body, exon, intron) from GTF and use region weights (default: False)
            upstream_size: Promoter size in bp when use_sp_regions=True (default: 5000)
            downstream_size: Terminator size in bp when use_sp_regions=True (default: 2000)
            min_intron_size: Minimum intron length when use_sp_regions=True (default: 0)
            max_gap: Max gap for grouping unknown-region DMPs (default: 1)
            w_promoter, w_terminator, w_gene_body, w_exon, w_intron, w_unknown: BED region scores when use_sp_regions=True (Stouffer weighting only)
            biology_weights: Per (feature × hyper/hypo) weights for canonical gene/feature importance
            storey_lambda: If set, use this single lambda for Storey FDR (for SP parity). If None, use automatic lambda (default).
        """
        self.gene_gtf = Path(gene_gtf)
        if not self.gene_gtf.exists():
            raise FileNotFoundError(f"GTF file not found: {self.gene_gtf}")
        
        # None or [] = intersect all GTF feature types (no post-filter)
        self.feature_types = feature_types if feature_types else None
        # Preserve explicitly configured detailed feature labels as first-class output identity.
        # Parent-bucket rollup for scoring/hits can still operate independently.
        configured = feature_types if feature_types else []
        self._explicit_feature_labels = {
            self._canonical_feature_token(v) for v in configured if str(v).strip()
        }
        self.auxiliary_bed_paths = [Path(p).expanduser() for p in (auxiliary_bed_paths or []) if p]
        self.run_bedtools_closest = bool(run_bedtools_closest)
        self.closest_gene_bed = Path(closest_gene_bed).expanduser() if closest_gene_bed else None
        self.use_p_value_weight = use_p_value_weight
        self.use_q_value_weight = use_q_value_weight
        self.use_effect_size_weight = use_effect_size_weight
        self.p_value_log_transform = p_value_log_transform

        # Disease enrichment
        self.enrich_disease = enrich_disease
        self.enrich_source = enrich_source
        self.separate_enrichment_sources = separate_enrichment_sources
        self.plant_traits_path = Path(plant_traits_path).expanduser() if plant_traits_path else None
        self.disease_enricher = None
        if enrich_disease:
            try:
                if self._is_plant_traits_source(enrich_source):
                    if not self.plant_traits_path:
                        raise ValueError(
                            "enrich_source=plant_traits requires plant_traits_path "
                            "(offline gene↔trait TSV/CSV)."
                        )
                    self.disease_enricher = PlantTraitEnricher(
                        plant_traits_path=self.plant_traits_path,
                        disease_term=disease_term,
                    )
                else:
                    use_grok, use_open_targets, use_disgenet = self._parse_enrich_source(enrich_source)
                    _grok_bs = min(20, max(1, int(grok_batch_size)))
                    self.disease_enricher = GeneDiseaseEnricher(
                        grok_api_key=grok_api_key if use_grok else None,
                        disgenet_api_key=disgenet_api_key if use_disgenet else None,
                        disease_term=disease_term,
                        use_grok=use_grok,
                        use_disgenet=use_disgenet,
                        use_open_targets=use_open_targets,
                        enrichment_profile=enrichment_profile,
                        min_evidence_level=min_evidence_level,
                        min_publications=min_publications,
                        min_disgenet_score=min_disgenet_score,
                        allow_predicted=allow_predicted,
                        cache_enabled=cache_enabled,
                        cache_dir=cache_dir,
                        cache_backend=cache_backend,
                        cache_db_path=cache_db_path,
                        cache_ttl_days=cache_ttl_days,
                        grok_cache_ttl_days=grok_cache_ttl_days,
                        source_max_workers=source_max_workers,
                        grok_batch_size=_grok_bs,
                        grok_max_workers=max(1, int(grok_max_workers)),
                        grok_use_xai_batch_api=grok_use_xai_batch_api,
                        grok_batch_poll_interval=grok_batch_poll_interval,
                        grok_batch_submit_chunk_size=grok_batch_submit_chunk_size,
                        rate_limit_delay=grok_rate_limit_delay,
                        max_retries=max(1, int(grok_max_retries)),
                        grok_429_inter_batch_sleep=float(grok_429_inter_batch_sleep),
                        open_targets_max_workers=open_targets_max_workers,
                        disgenet_max_workers=disgenet_max_workers,
                        azure_key_vault_url=azure_key_vault_url,
                        azure_secret_name=azure_secret_name,
                        encrypted_file_path=encrypted_file_path,
                        methyl_mapper_home=methyl_mapper_home,
                    )
            except Exception as e:
                logger.warning(f"Disease enricher initialization failed: {e}. Enrichment columns will not be added.")
                self.disease_enricher = None

        # DMP optimization parameters
        self.optimize_dmps = optimize_dmps
        self.dmp_rank_columns = dmp_rank_columns
        self.min_k = min_k
        self.max_k = max_k
        self.stability_threshold = stability_threshold
        self.unrelated_growth_threshold = unrelated_growth_threshold
        self.extend_after_stable = extend_after_stable

        # SP-equivalent region model
        self.use_sp_regions = use_sp_regions
        self.upstream_size = upstream_size
        self.downstream_size = downstream_size
        self.min_intron_size = min_intron_size
        self.max_gap = max_gap
        self.w_promoter = w_promoter
        self.w_terminator = w_terminator
        self.w_gene_body = w_gene_body
        self.w_exon = w_exon
        self.w_intron = w_intron
        self.w_unknown = w_unknown
        self.biology_weights = biology_weights if biology_weights is not None else BiologyWeightConfig()
        self.storey_lambda = storey_lambda
        self._sp_regions_bed_path: Optional[Path] = None
        self._genes_closest_bed_path: Optional[Path] = None

        # Check bedtools availability
        try:
            subprocess.run(['bedtools', '--version'], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise RuntimeError("bedtools not found. Please install bedtools: conda install -c bioconda bedtools")
    
    def csv_to_bed(self, csv_path: Path, output_bed: Optional[Path] = None) -> Path:
        """
        Convert MethylDetector CSV to BED format.
        
        Args:
            csv_path: Path to DMP CSV file
            output_bed: Optional output BED file path. If None, creates temp file.
            
        Returns:
            Path to BED file
        """
        csv_path = Path(csv_path)
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV file not found: {csv_path}")
        
        if output_bed is None:
            output_bed = Path(tempfile.mkdtemp()) / f"{csv_path.stem}.bed"
        
        logger.debug(f"Converting {csv_path} to BED format...")
        
        # Read CSV and normalize column names (p-value -> p_value, Effect Size -> effect_size, etc.)
        df = pd.read_csv(csv_path)
        df = self._normalize_dmp_columns(df)

        # Check required columns
        required_cols = ['chromosome', 'position']
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")
        
        # Normalize chromosome for GTF compatibility (GENCODE/UCSC use chr1, chr2, ... chrX, chrY)
        def _bed_chrom(c: str) -> str:
            s = str(c).strip()
            if not s.startswith('chr'):
                return f'chr{s}' if s else s
            return s

        chrom_series = df['chromosome'].astype(str).str.strip()
        pos_series = pd.to_numeric(df['position'], errors='raise').astype(np.uint32)
        name_series = chrom_series + ":" + pos_series.astype(str)
        if 'context' in df.columns:
            name_series = name_series + ":" + df['context'].astype(str)
        if 'effect_size' in df.columns:
            eff_series = pd.to_numeric(df['effect_size'], errors='coerce').fillna(0.0).map(lambda v: f"eff={v:.3f}")
            name_series = name_series + ":" + eff_series

        bed_df = pd.DataFrame(
            {
                'chrom': chrom_series.map(_bed_chrom),
                'start': pos_series - 1,
                'end': pos_series,
                'name': name_series,
            }
        )
        
        # Write BED file (sorted for bedtools)
        bed_df = bed_df.sort_values(['chrom', 'start'])
        bed_df.to_csv(output_bed, sep='\t', header=False, index=False)
        
        logger.debug(f"Created BED file: {output_bed} ({len(bed_df)} entries)")
        return output_bed
    
    def _get_sp_regions_bed(self, temp_dir: Optional[Path] = None) -> Path:
        """Build or return cached path to SP-equivalent regions BED."""
        if self._sp_regions_bed_path is not None and self._sp_regions_bed_path.exists():
            return self._sp_regions_bed_path
        base = temp_dir or Path(tempfile.gettempdir())
        out = base / "sp_regions.bed"
        build_sp_regions_bed(
            self.gene_gtf,
            out,
            upstream_size=self.upstream_size,
            downstream_size=self.downstream_size,
            min_intron_size=self.min_intron_size,
            w_promoter=self.w_promoter,
            w_terminator=self.w_terminator,
            w_gene_body=self.w_gene_body,
            w_exon=self.w_exon,
            w_intron=self.w_intron,
        )
        self._sp_regions_bed_path = out
        return out

    def intersect_with_features(
        self,
        bed_path: Path,
        dmp_df: pd.DataFrame,
        output_file: Optional[Path] = None
    ) -> pd.DataFrame:
        """
        Intersect DMP BED file with GTF features or SP-equivalent regions BED using bedtools.

        When use_sp_regions is True, intersects with a generated BED of promoter, terminator,
        gene body, exon, intron regions (with weights) and adds region_weight / combined_weight.

        Args:
            bed_path: Path to DMP BED file
            dmp_df: Original DMP DataFrame (for joining weights)
            output_file: Optional output file path

        Returns:
            DataFrame with DMP-feature intersections
        """
        if self.use_sp_regions:
            return self._intersect_with_sp_regions(bed_path, dmp_df, output_file)

        logger.info(f"Intersecting DMPs with features from {self.gene_gtf}...")

        cmd = [
            'bedtools', 'intersect',
            '-a', str(bed_path),
            '-b', str(self.gene_gtf),
            '-wa', '-wb'
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"bedtools intersect failed: {e.stderr}")
            raise

        lines = result.stdout.strip().split('\n')
        if not lines or lines == ['']:
            logger.warning("No intersections found!")
            return self._finalize_intersection_table(pd.DataFrame(), bed_path, dmp_df, output_file)

        intersections = []
        for line in lines:
            parts = line.split('\t')
            if len(parts) < 13:
                continue
            dmp_name = parts[3]
            feature_chrom = parts[4]
            feature_source = parts[5]
            feature_type = parts[6]
            feature_start = int(parts[7])
            feature_end = int(parts[8])
            feature_score = parts[9]
            feature_strand = parts[10]
            feature_frame = parts[11]
            feature_attrs = parts[12]
            attrs = self._parse_gtf_attributes(feature_attrs)
            intersections.append({
                'dmp_name': dmp_name,
                'feature_chrom': feature_chrom,
                'feature_source': feature_source,
                'feature_type': feature_type,
                'feature_start': feature_start,
                'feature_end': feature_end,
                'feature_strand': feature_strand,
                'feature_frame': feature_frame,
                'gene_id': attrs.get('gene_id', ''),
                'gene_name': attrs.get('gene_name', attrs.get('gene_id', '')),
                'transcript_id': attrs.get('transcript_id', ''),
                'transcript_name': attrs.get('transcript_name', attrs.get('transcript_id', '')),
                'exon_id': attrs.get('exon_id', ''),
                'exon_number': attrs.get('exon_number', ''),
                'attributes': feature_attrs
            })

        intersect_df = pd.DataFrame(intersections)
        if self.feature_types:
            intersect_df = intersect_df[intersect_df['feature_type'].isin(self.feature_types)]
        logger.info(f"Found {len(intersect_df)} DMP-feature intersections")
        intersect_df = self._join_with_dmp_weights(intersect_df, dmp_df)
        return self._finalize_intersection_table(intersect_df, bed_path, dmp_df, output_file)

    def _intersect_with_sp_regions(
        self,
        bed_path: Path,
        dmp_df: pd.DataFrame,
        output_file: Optional[Path] = None,
    ) -> pd.DataFrame:
        """Intersect DMP BED with SP-equivalent regions BED; add region_weight and gene_id/gene_name/feature_type."""
        regions_bed = self._get_sp_regions_bed(bed_path.parent)
        logger.info(f"Intersecting DMPs with SP regions from {regions_bed}...")
        cmd = [
            'bedtools', 'intersect',
            '-a', str(bed_path),
            '-b', str(regions_bed),
            '-wa', '-wb',
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"bedtools intersect failed: {e.stderr}")
            raise
        lines = result.stdout.strip().split('\n')
        if not lines or lines == ['']:
            logger.warning("No intersections found!")
            intersect_df = pd.DataFrame()
        else:
            rows = []
            for line in lines:
                parts = line.split('\t')
                if len(parts) < 10:
                    continue
                dmp_name = parts[3]
                # BED B: chrom, start, end, name, score, strand
                r_chrom, r_start, r_end, r_name, r_score, r_strand = parts[4], parts[5], parts[6], parts[7], parts[8], parts[9]
                try:
                    region_weight = float(r_score)
                except (ValueError, TypeError):
                    region_weight = 1.0
                gene_id, gene_name, feature_type = parse_region_name(r_name)
                rows.append({
                    'dmp_name': dmp_name,
                    'feature_chrom': r_chrom,
                    'feature_type': feature_type,
                    'feature_start': int(r_start),
                    'feature_end': int(r_end),
                    'feature_strand': r_strand,
                    'gene_id': gene_id,
                    'gene_name': gene_name or gene_id,
                    'region_weight': region_weight,
                })
            intersect_df = pd.DataFrame(rows)
        logger.info(f"Found {len(intersect_df)} DMP-region intersections (SP model)")
        intersect_df = self._join_with_dmp_weights(intersect_df, dmp_df)
        if 'region_weight' in intersect_df.columns and 'weight' in intersect_df.columns:
            intersect_df['combined_weight'] = intersect_df['weight'] * intersect_df['region_weight']
        return self._finalize_intersection_table(intersect_df, bed_path, dmp_df, output_file)

    def _sanitize_aux_bed_label(self, path: Path) -> str:
        s = re.sub(r"[^0-9a-zA-Z]+", "_", path.stem).strip("_").lower()
        return (s or "aux")[:56]

    def _get_or_build_genes_closest_bed(self, work_dir: Path) -> Path:
        if self.closest_gene_bed is not None and self.closest_gene_bed.exists():
            return self.closest_gene_bed
        if self._genes_closest_bed_path is not None and self._genes_closest_bed_path.exists():
            return self._genes_closest_bed_path
        out = Path(work_dir) / "_methylmapper_genes_closest.bed"
        build_gene_bodies_bed(self.gene_gtf, out)
        self._genes_closest_bed_path = out
        return out

    def _per_dmp_auxiliary_overlaps(self, dmp_bed: Path) -> pd.DataFrame:
        """One row per dmp_name with aux_<label>_regions and aux_<label>_n_hits columns."""
        if not self.auxiliary_bed_paths:
            return pd.DataFrame()
        from collections import defaultdict

        region_keys = [
            f"aux_{self._sanitize_aux_bed_label(aux)}_regions"
            for aux in self.auxiliary_bed_paths
            if aux.exists()
        ]
        if not region_keys:
            return pd.DataFrame(columns=["dmp_name"])

        hits: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
        for aux in self.auxiliary_bed_paths:
            if not aux.exists():
                logger.warning("Auxiliary BED not found, skipping: %s", aux)
                continue
            label = self._sanitize_aux_bed_label(aux)
            rkey = f"aux_{label}_regions"
            cmd = ["bedtools", "intersect", "-a", str(dmp_bed), "-b", str(aux), "-wa", "-wb"]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            except subprocess.CalledProcessError as e:
                logger.error("bedtools intersect auxiliary failed (%s): %s", aux, e.stderr)
                raise
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                dmp_name = parts[3]
                b_off = 4
                bn = len(parts) - b_off
                if bn >= 4:
                    hit_label = parts[b_off + 3]
                else:
                    hit_label = f"{parts[b_off]}:{parts[b_off + 1]}-{parts[b_off + 2]}"
                hits[dmp_name][rkey].append(hit_label)

        rows = []
        for dmp_name, colmap in hits.items():
            row: Dict = {"dmp_name": dmp_name}
            for lbl in region_keys:
                vs = colmap.get(lbl, [])
                row[lbl] = "|".join(dict.fromkeys(vs))
                row[lbl.replace("_regions", "_n_hits")] = len(vs)
            rows.append(row)
        return pd.DataFrame(rows)

    def _per_dmp_closest_genes(self, dmp_bed: Path, work_dir: Path) -> pd.DataFrame:
        """bedtools closest: nearest gene body per DMP (distance in bp)."""
        gene_bed = self._get_or_build_genes_closest_bed(work_dir)
        cmd = [
            "bedtools", "closest",
            "-a", str(dmp_bed),
            "-b", str(gene_bed),
            "-D", "b",
            "-t", "first",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as e:
            logger.error("bedtools closest failed: %s", e.stderr)
            raise
        rows = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 11:
                continue
            dmp_name = parts[3]
            b_name = parts[7]
            dist_raw = parts[-1]
            try:
                dist_bp = int(dist_raw)
            except ValueError:
                dist_bp = None
            gid, gname, _ = parse_region_name(b_name) if "|" in b_name else (b_name, b_name, "")
            rows.append({
                "dmp_name": dmp_name,
                "closest_gene_id": gid,
                "closest_gene_name": gname or gid,
                "closest_gene_distance_bp": dist_bp,
            })
        if not rows:
            return pd.DataFrame(
                columns=["dmp_name", "closest_gene_id", "closest_gene_name", "closest_gene_distance_bp"]
            )
        return pd.DataFrame(rows).drop_duplicates(subset=["dmp_name"], keep="first")

    def _dmp_names_from_bed(self, dmp_bed: Path) -> pd.DataFrame:
        raw = pd.read_csv(dmp_bed, sep="\t", header=None, usecols=[0, 1, 2, 3], names=["_c", "_s", "_e", "dmp_name"])
        return raw[["dmp_name"]].drop_duplicates()

    def _finalize_intersection_table(
        self,
        intersect_df: pd.DataFrame,
        bed_path: Path,
        dmp_df: pd.DataFrame,
        output_file: Optional[Path],
    ) -> pd.DataFrame:
        """Merge auxiliary BED and closest-gene annotations; handle empty GTF intersect."""
        work_dir = bed_path.parent
        extra_dfs: List[pd.DataFrame] = []
        if self.auxiliary_bed_paths:
            aux_df = self._per_dmp_auxiliary_overlaps(bed_path)
            if not aux_df.empty:
                extra_dfs.append(aux_df)
        if self.run_bedtools_closest:
            clo_df = self._per_dmp_closest_genes(bed_path, work_dir)
            if not clo_df.empty:
                extra_dfs.append(clo_df)

        if intersect_df.empty and not extra_dfs:
            if output_file:
                intersect_df.to_csv(output_file, index=False)
            return intersect_df

        if intersect_df.empty and extra_dfs:
            base = self._dmp_names_from_bed(bed_path)
            base = self._join_with_dmp_weights(base, dmp_df)
            for edf in extra_dfs:
                base = base.merge(edf, on="dmp_name", how="left")
            logger.info("No GTF intersections; exported DMP rows with auxiliary/closest annotations only")
            if output_file:
                base.to_csv(output_file, index=False)
                logger.info("Saved intersections to %s", output_file)
            return base

        out = intersect_df
        for edf in extra_dfs:
            if not edf.empty:
                out = out.merge(edf, on="dmp_name", how="left")
        if output_file:
            out.to_csv(output_file, index=False)
            logger.info("Saved intersections to %s", output_file)
        return out
    
    def _parse_gtf_attributes(self, attr_string: str) -> Dict[str, str]:
        """Parse GTF attributes string into dictionary."""
        attrs = {}
        for item in attr_string.split(';'):
            item = item.strip()
            if not item:
                continue
            if ' ' in item:
                key, value = item.split(' ', 1)
                key = key.strip()
                value = value.strip().strip('"')
                attrs[key] = value
        return attrs
    
    @staticmethod
    def _normalize_chrom_for_join(chrom_series: pd.Series) -> pd.Series:
        """Normalize chromosome to canonical form (chr1, chr2, ..., chr22, chrX, chrY) for join.
        DMPs typically use 1, 2, ..., 22, X, Y without prefix; add 'chr' so join matches either format.
        Coerce 1.0 -> 1 so float and int CSV columns match the BED name.
        """
        s = chrom_series.astype(str).str.strip()
        # Normalize numeric-looking chromosomes (e.g. "1.0" -> "1") so BED and CSV join keys match
        def _canon(s: str) -> str:
            if not s or s.startswith('chr'):
                return s
            try:
                x = float(s)
                if x == int(x):
                    return str(int(x))
            except (ValueError, TypeError):
                pass
            return s
        s = s.map(_canon)
        return s.where(s.str.startswith('chr') | (s.str.len() == 0), 'chr' + s)

    @staticmethod
    def _normalize_context_for_join(context_series: pd.Series) -> pd.Series:
        """Normalize context to CG, CHG, CHH (uppercase, stripped) for join."""
        return context_series.astype(str).str.strip().str.upper()

    @staticmethod
    def _direction_to_sign(direction_series: pd.Series) -> pd.Series:
        """Map textual/encoded direction to sign (-1, +1), NaN when unknown."""
        raw = direction_series.astype(str).str.strip().str.lower()
        out = pd.Series(np.nan, index=direction_series.index, dtype=float)

        pos_tokens = {
            "hyper", "up", "increase", "increased", "higher",
            "positive", "pos", "plus", "+", "1", "1.0",
        }
        neg_tokens = {
            "hypo", "down", "decrease", "decreased", "lower",
            "negative", "neg", "minus", "-", "-1", "-1.0",
        }
        out[raw.isin(pos_tokens)] = 1.0
        out[raw.isin(neg_tokens)] = -1.0

        unresolved = out.isna()
        if unresolved.any():
            numeric = pd.to_numeric(raw[unresolved], errors="coerce")
            out.loc[unresolved & numeric.notna()] = np.sign(numeric[numeric.notna()]).astype(float)

        return out

    @staticmethod
    def _normalize_dmp_columns(df: pd.DataFrame) -> pd.DataFrame:
        """Normalize DMP CSV column names so variants (p-value, Effect Size, etc.) map to expected names."""
        if df.empty:
            return df
        rename = {}
        for c in df.columns:
            canonical = str(c).strip().lower().replace(' ', '_').replace('-', '_')
            if canonical in ('p_value', 'pvalue'):
                rename[c] = 'p_value'
            elif canonical in ('q_value', 'qvalue'):
                rename[c] = 'q_value'
            elif canonical in ('effect_size', 'effectsize'):
                rename[c] = 'effect_size'
            elif canonical in ('delta_mean', 'deltamean'):
                rename[c] = 'delta_mean'
            elif canonical in ('chromosome', 'chrom', 'chr'):
                rename[c] = 'chromosome'
            elif canonical in ('position', 'pos'):
                rename[c] = 'position'
            elif canonical == 'context':
                rename[c] = 'context'
            elif canonical in ('direction', 'delta_direction', 'methylation_direction', 'sign'):
                rename[c] = 'direction'
        if rename:
            df = df.rename(columns=rename)
        df = BedtoolsMapper._recover_effect_size_column(df)
        return df

    @staticmethod
    def _recover_effect_size_column(df: pd.DataFrame) -> pd.DataFrame:
        """Populate/repair effect_size using alternate exported weight columns when needed.

        Some upstream tables carry usable effect proxies (e.g. ``weight`` or
        ``effect_size_mean``) but either omit ``effect_size`` or provide a flat
        fallback value (commonly all ``1.0``). Prefer a varying numeric source so
        downstream ranking and weighting keep MethylDetector signal strength.
        """
        out = df.copy()

        def _coerce_numeric(col_name: str) -> Optional[pd.Series]:
            if col_name not in out.columns:
                return None
            return pd.to_numeric(out[col_name], errors="coerce")

        eff = _coerce_numeric("effect_size")
        has_effect = eff is not None and np.isfinite(eff).any()
        effect_is_flat_one = bool(
            has_effect and np.isclose(eff[np.isfinite(eff)], 1.0).all()
        )

        # Preferred explicit aliases before generic fallbacks.
        candidate_cols = [
            "effect_size_approx",
            "effect_size_mean",
            "effect_size_max",
            "effect_size_sum",
            "importance",
            "weight",
        ]
        for col in candidate_cols:
            vals = _coerce_numeric(col)
            if vals is None or not np.isfinite(vals).any():
                continue
            if effect_is_flat_one and np.isclose(vals[np.isfinite(vals)], 1.0).all():
                continue
            if (not has_effect) or effect_is_flat_one:
                out["effect_size"] = vals.astype(float)
                return out

        per_comparison_cols = [c for c in out.columns if str(c).startswith("effect_size__")]
        if per_comparison_cols and ((not has_effect) or effect_is_flat_one):
            wide_vals = out[per_comparison_cols].apply(pd.to_numeric, errors="coerce")
            row_max = wide_vals.max(axis=1, skipna=True)
            if np.isfinite(row_max).any():
                out["effect_size"] = row_max.astype(float)

        return out

    @staticmethod
    def _build_dmp_name_series(df: pd.DataFrame) -> pd.Series:
        """Build the same name string as csv_to_bed uses for the BED 4th column. Used for exact join."""
        chrom = df['chromosome'].astype(str).str.strip()
        pos = pd.to_numeric(df['position'], errors='coerce')
        # Same as csv_to_bed: integer position, no decimal
        pos_int = pos.fillna(0).astype(np.uint32)
        name = chrom + ":" + pos_int.astype(str)
        if 'context' in df.columns:
            name = name + ":" + df['context'].astype(str)
        if 'effect_size' in df.columns:
            eff = pd.to_numeric(df['effect_size'], errors='coerce').fillna(0.0)
            name = name + ":" + eff.map(lambda v: f"eff={v:.3f}")
        return name

    def _join_with_dmp_weights(self, intersect_df: pd.DataFrame, dmp_df: pd.DataFrame) -> pd.DataFrame:
        """Join intersection DataFrame with DMP weights. Uses exact BED name (dmp_name) as join key so
        intersection rows match the same CSV rows that produced the BED, avoiding chrom/position/context parsing mismatches.
        """
        dmp_df = self._normalize_dmp_columns(dmp_df.copy())
        intersect_df = intersect_df.reset_index(drop=True).copy()

        # Build dmp_name in DMP table exactly as csv_to_bed does, so we can join on it
        if 'chromosome' not in dmp_df.columns or 'position' not in dmp_df.columns:
            logger.warning("DMP table missing chromosome or position; cannot build join key.")
            return intersect_df
        dmp_lookup = dmp_df.copy()
        dmp_lookup['chromosome'] = dmp_lookup['chromosome'].astype(str).str.strip()
        dmp_lookup['position'] = (
            pd.to_numeric(dmp_lookup['position'], errors='coerce').fillna(0).astype(np.uint32)
        )
        dmp_lookup['dmp_name'] = self._build_dmp_name_series(dmp_lookup)

        # When duplicate dmp_name exist, keep the row that has stats
        stat_ok = pd.Series(False, index=dmp_lookup.index)
        for c in ['p_value', 'effect_size']:
            if c in dmp_lookup.columns:
                vals = pd.to_numeric(dmp_lookup[c], errors='coerce')
                stat_ok = stat_ok | (vals.notna() & np.isfinite(vals))
        dmp_lookup = dmp_lookup.assign(_stat_ok=stat_ok).sort_values('_stat_ok', ascending=False).drop(columns=['_stat_ok'])
        dmp_lookup = dmp_lookup.drop_duplicates(subset=['dmp_name'], keep='first')

        optional_cols = [
            'p_value', 'q_value', 'effect_size', 'delta_mean',
            'context', 'importance', 'weight', 'overlap', 'frequency'
        ]
        stat_cols = ['p_value', 'q_value', 'effect_size', 'delta_mean']
        existing_cols = [c for c in optional_cols if c in dmp_lookup.columns]
        if not any(c in dmp_lookup.columns for c in stat_cols):
            logger.warning(
                "DMP table has no stat columns (p_value, q_value, effect_size, delta_mean). "
                "Expected headers include 'p_value'/'p-value', 'q_value'/'q-value', 'effect_size'/'effect size', 'delta_mean'/'delta mean'. "
                "Gene aggregation stats will be missing."
            )
        merge_cols = ['dmp_name'] + [c for c in existing_cols if c != 'dmp_name']
        merged = intersect_df.merge(
            dmp_lookup[merge_cols],
            on='dmp_name',
            how='left',
            suffixes=('', '_dmp')
        )

        # Warn if join left key stats missing (column names OK but keys may not align)
        n_rows = len(merged)
        if n_rows > 0:
            for col in ['p_value', 'effect_size']:
                if col in merged.columns:
                    n_missing = merged[col].isna().sum()
                    if n_missing == n_rows:
                        logger.warning(
                            "All %d intersection rows have missing '%s' after joining DMP data. "
                            "Check that chromosome, position, and context in the DMP CSV match the BED (e.g. chromosome as '1' or 'chr1' consistently).",
                            n_rows, col
                        )
                        break
                    elif n_missing > 0 and n_missing >= max(1, 0.1 * n_rows):
                        logger.warning(
                            "%.1f%% of intersection rows (%d/%d) have missing '%s' after joining DMP data. "
                            "Check chromosome/position/context alignment between DMP CSV and BED.",
                            100.0 * n_missing / n_rows, n_missing, n_rows, col
                        )
                        break

        # Compute weighted scores. p_value/q_value can be 0 or near-zero (clip to 1e-300 to avoid inf);
        # effect_size is always > 0. Use finite fallbacks so NaN/inf do not zero out a gene's total_weight.
        merged['weight'] = 1.0

        if self.use_p_value_weight and 'p_value' in merged.columns:
            pv = merged['p_value'].clip(lower=1e-300)  # 0 or rounded zero -> finite weight
            if self.p_value_log_transform:
                merged['p_weight'] = -np.log10(pv)
            else:
                merged['p_weight'] = 1.0 / pv
            merged['p_weight'] = merged['p_weight'].replace([np.inf, -np.inf], np.nan).fillna(1.0)
            merged['weight'] = merged['weight'] * merged['p_weight']

        if self.use_q_value_weight and 'q_value' in merged.columns:
            qv = merged['q_value'].clip(lower=1e-300)
            merged['q_weight'] = -np.log10(qv).replace([np.inf, -np.inf], np.nan).fillna(1.0)
            merged['weight'] = merged['weight'] * merged['q_weight']

        if self.use_effect_size_weight:
            eff_col = None
            if 'effect_size' in merged.columns:
                eff_col = 'effect_size'
            elif 'importance' in merged.columns:
                eff_col = 'importance'
            elif 'delta_mean' in merged.columns:
                eff_col = 'delta_mean'

            if eff_col is not None:
                merged['eff_weight'] = merged[eff_col].abs().fillna(1.0)
                merged['weight'] = merged['weight'] * merged['eff_weight']

        # Ensure no NaN from failed join or missing p_value/q_value: every row contributes
        merged['weight'] = merged['weight'].fillna(1.0)

        # Normalize weights (optional - can be disabled)
        w_max = merged['weight'].max()
        if w_max is not None and np.isfinite(w_max) and w_max > 0:
            merged['weight'] = merged['weight'] / w_max

        return merged

    @staticmethod
    def _is_plant_traits_source(enrich_source: str) -> bool:
        """True when enrichment uses the offline plant gene↔trait table (not human APIs)."""
        normalized = (enrich_source or "").lower().strip()
        if normalized in {"plant_traits", "plant-traits"}:
            return True
        tokens = [t for t in re.split(r"[+/,\s]+", normalized) if t]
        return any(t in {"plant_traits", "plant-traits"} for t in tokens)

    @staticmethod
    def _parse_enrich_source(enrich_source: str) -> Tuple[bool, bool, bool]:
        """Parse enrich_source into flags for grok/open_targets/disgenet.

        ``plant_traits`` is handled separately via :meth:`_is_plant_traits_source`
        (returns all-False here so Open Targets / DisGeNET are never enabled).
        """
        normalized = (enrich_source or "").lower().strip()

        if BedtoolsMapper._is_plant_traits_source(normalized):
            return False, False, False

        if normalized == "all":
            # Grok + Open Targets only (DisGeNET must be requested explicitly)
            return True, True, False
        if normalized == "both":
            return True, True, False

        # Split on separators and whitespace. Use \s (not literal "s"), so
        # values like "opentargets" are not truncated to "opentarget".
        tokens = [t for t in re.split(r"[+/,\s]+", normalized) if t]
        use_grok = "grok" in tokens
        use_disgenet = "disgenet" in tokens
        use_open_targets = any(
            t in tokens for t in ["opentargets", "opentarget", "open_targets", "open-targets"]
        )

        return use_grok, use_open_targets, use_disgenet

    def _sort_dmps_for_optimization(self, dmp_df: pd.DataFrame) -> pd.DataFrame:
        """Sort DMPs by importance for optimization."""
        df = dmp_df.copy()
        rank_cols = self.dmp_rank_columns or ['effect_size', 'importance', 'delta_mean', 'weight']
        rank_col = next((c for c in rank_cols if c in df.columns), None)

        if rank_col is None:
            logger.warning("No ranking column found for DMP optimization; using input order")
            return df

        sort_keys = []
        ascending = []

        if rank_col in ['effect_size', 'delta_mean']:
            df['_rank_key'] = df[rank_col].abs()
            sort_keys.append('_rank_key')
            ascending.append(False)
        else:
            sort_keys.append(rank_col)
            ascending.append(False)

        if 'q_value' in df.columns:
            sort_keys.append('q_value')
            ascending.append(True)
        if 'p_value' in df.columns:
            sort_keys.append('p_value')
            ascending.append(True)

        df = df.sort_values(sort_keys, ascending=ascending).reset_index(drop=True)
        if '_rank_key' in df.columns:
            df = df.drop(columns=['_rank_key'])

        logger.info(f"Sorting DMPs by {rank_col} for optimization")
        return df

    @classmethod
    def _canonical_feature_token(cls, value: str) -> str:
        raw = str(value).strip().lower()
        raw = raw.replace("-", "_").replace(" ", "_")
        raw = re.sub(r"_+", "_", raw)
        raw = raw.strip("_")
        aliases = {
            "genebody": "gene_body",
            "gene-body": "gene_body",
            "body_gene": "gene_body",
        }
        return aliases.get(raw, raw)

    @classmethod
    def _parent_bucket_for_feature(cls, token: str) -> str:
        canonical = cls._canonical_feature_token(token)
        if canonical in cls._PARENT_FEATURES:
            return canonical
        mapped = cls._DETAILED_TO_PARENT.get(canonical)
        if mapped in cls._PARENT_FEATURES:
            return str(mapped)
        # Conservative fallback: keep DMP contribution in-gene instead of dropping from parent summaries.
        return "gene_body"

    def _normalize_feature_type(self, value: str) -> str:
        """
        Resolve feature label for parent-bucket aggregation.

        - If a detailed token is explicitly configured in feature_types, preserve it as-is.
        - Otherwise roll up detailed annotations to parent buckets.
        """
        canonical = self._canonical_feature_token(value)
        explicit = getattr(self, "_explicit_feature_labels", set()) or set()
        if canonical and canonical in explicit and canonical not in self._PARENT_FEATURES:
            return canonical
        return self._parent_bucket_for_feature(canonical)

    def _compute_exclusive_feature_hits(self, intersect_df: pd.DataFrame, group_by: str) -> pd.DataFrame:
        """Per (DMP, gene) exclusive assignment by priority for stable hit counts."""
        work = self._exclusive_feature_rows(intersect_df, group_by=group_by)
        if work.empty:
            return pd.DataFrame(columns=[group_by])

        work = work[[group_by, "dmp_name", "feature_norm"]].copy()
        work = work[work["feature_norm"].isin(self._FEATURE_HITS_COLS.keys())]
        if work.empty:
            return pd.DataFrame(columns=[group_by])

        counts = (
            work.groupby([group_by, "feature_norm"])
            .size()
            .unstack(fill_value=0)
            .reset_index()
        )
        rename_map = {k: v for k, v in self._FEATURE_HITS_COLS.items() if k in counts.columns}
        counts = counts.rename(columns=rename_map)
        for col in self._FEATURE_HITS_COLS.values():
            if col not in counts.columns:
                counts[col] = 0
        return counts[[group_by, "hits_promoter", "hits_exon", "hits_intron", "hits_gene_body", "hits_terminator"]]

    def _exclusive_feature_rows(self, intersect_df: pd.DataFrame, group_by: str) -> pd.DataFrame:
        """Return one row per (group_by, dmp_name) using feature priority."""
        if (
            group_by not in intersect_df.columns
            or "dmp_name" not in intersect_df.columns
            or "feature_type" not in intersect_df.columns
        ):
            return pd.DataFrame()
        work = intersect_df.copy()
        work["feature_norm"] = work["feature_type"].map(self._normalize_feature_type)
        work["feature_priority"] = work["feature_norm"].map(self._FEATURE_PRIORITY).fillna(99).astype(int)
        work = work.sort_values(["feature_priority"], ascending=True)
        work = work.drop_duplicates(subset=[group_by, "dmp_name"], keep="first")
        return work

    def _biology_feature_bucket(self, feature_norm: str) -> str:
        bucket = str(feature_norm or "gene_body")
        if bucket in self._FEATURE_SCORE_ORDER:
            return bucket
        return self._parent_bucket_for_feature(bucket)

    def _row_biology_weight(self, feature_norm: str, effect_sign: float) -> float:
        bucket = self._biology_feature_bucket(feature_norm)
        cfg = self.biology_weights
        if effect_sign > 0:
            return cfg.weight_for(bucket, hyper=True)
        if effect_sign < 0:
            return cfg.weight_for(bucket, hyper=False)
        hyper_w = cfg.weight_for(bucket, hyper=True)
        hypo_w = cfg.weight_for(bucket, hyper=False)
        return (hyper_w + hypo_w) / 2.0

    def _build_compound_effect_metrics(
        self,
        score_source_df: pd.DataFrame,
        group_by: str,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """
        Build deduplicated DMP-like biological importance metrics.

        Gene-level dedup key is (group_by, dmp_name) from score_source_df.
        Feature-level dedup key is (group_by, feature_norm, dmp_name).

        Per-DMP weight: w_i = frequency_i × bio_weight(feature_i, hyper|hypo).
        """
        if score_source_df is None or score_source_df.empty or "effect_size" not in score_source_df.columns:
            return pd.DataFrame(columns=[group_by]), pd.DataFrame(columns=[group_by])

        cols = [
            c
            for c in (group_by, "dmp_name", "feature_norm", "effect_size", "delta_mean", "frequency")
            if c in score_source_df.columns
        ]
        work = score_source_df[cols].copy()
        if group_by not in work.columns:
            return pd.DataFrame(columns=[group_by]), pd.DataFrame(columns=[group_by])

        work["effect_size"] = pd.to_numeric(work["effect_size"], errors="coerce")
        work = work[np.isfinite(work["effect_size"])].copy()
        if work.empty:
            return pd.DataFrame(columns=[group_by]), pd.DataFrame(columns=[group_by])

        if "feature_norm" not in work.columns:
            work["feature_norm"] = "gene_body"

        if "frequency" in work.columns:
            freq_raw = pd.to_numeric(work["frequency"], errors="coerce").fillna(1.0)
            freq_weight = freq_raw.clip(lower=0.0)
            freq_support = freq_raw.clip(lower=0.0, upper=1.0)
        else:
            freq_weight = pd.Series(1.0, index=work.index, dtype=float)
            freq_support = pd.Series(1.0, index=work.index, dtype=float)
        work["frequency_weight"] = freq_weight
        work["frequency_support"] = freq_support

        if "delta_mean" in work.columns:
            sign_vals = np.sign(pd.to_numeric(work["delta_mean"], errors="coerce").fillna(0.0).to_numpy(dtype=float))
            eff_sign = np.sign(pd.to_numeric(work["effect_size"], errors="coerce").fillna(0.0).to_numpy(dtype=float))
            sign_vals = np.where(sign_vals == 0.0, eff_sign, sign_vals)
        else:
            sign_vals = np.sign(pd.to_numeric(work["effect_size"], errors="coerce").fillna(0.0).to_numpy(dtype=float))
        sign_vals = np.where(np.isfinite(sign_vals), sign_vals, 0.0)
        work["effect_sign"] = sign_vals

        work["direction_bio_weight"] = [
            self._row_biology_weight(fn, float(s))
            for fn, s in zip(work["feature_norm"].astype(str), work["effect_sign"].to_numpy(dtype=float))
        ]
        work["bio_weight"] = work["frequency_weight"] * pd.to_numeric(
            work["direction_bio_weight"], errors="coerce"
        ).fillna(1.0)
        work["abs_effect"] = np.abs(pd.to_numeric(work["effect_size"], errors="coerce").fillna(0.0))
        work["abs_term"] = work["bio_weight"] * work["abs_effect"]
        work["signed_term"] = work["abs_term"] * work["effect_sign"]

        gene_grouped = work.groupby(group_by, dropna=False)
        gene_metrics = gene_grouped.agg(
            _sum_weight=("bio_weight", "sum"),
            _sum_abs_term=("abs_term", "sum"),
            _sum_signed_term=("signed_term", "sum"),
            gene_support_freq=("frequency_support", "mean"),
        ).reset_index()
        if "dmp_name" in work.columns:
            support_n = (
                work[[group_by, "dmp_name"]]
                .dropna(subset=[group_by])
                .drop_duplicates(subset=[group_by, "dmp_name"])
                .groupby(group_by, dropna=False)["dmp_name"]
                .size()
                .reset_index(name="gene_support_n")
            )
            gene_metrics = gene_metrics.merge(support_n, on=group_by, how="left")
        else:
            gene_metrics["gene_support_n"] = gene_grouped.size().to_numpy(dtype=int)
        gene_metrics["gene_support_n"] = pd.to_numeric(gene_metrics["gene_support_n"], errors="coerce").fillna(0).astype(int)

        gene_metrics["gene_effect_abs_wsum"] = pd.to_numeric(gene_metrics["_sum_abs_term"], errors="coerce").fillna(0.0)
        sum_weight = pd.to_numeric(gene_metrics["_sum_weight"], errors="coerce").fillna(0.0)
        sum_abs = pd.to_numeric(gene_metrics["_sum_abs_term"], errors="coerce").fillna(0.0)
        sum_signed = pd.to_numeric(gene_metrics["_sum_signed_term"], errors="coerce").fillna(0.0)
        gene_metrics["gene_effect_signed_wsum"] = sum_signed
        gene_metrics["gene_direction"] = np.sign(sum_signed)
        gene_metrics["gene_effect_abs_wmean"] = np.where(sum_weight > 0.0, sum_abs / sum_weight, 0.0)
        gene_metrics["gene_direction_coherence"] = np.where(sum_abs > 0.0, np.abs(sum_signed) / sum_abs, 0.0)
        gene_metrics["gene_support_freq"] = pd.to_numeric(gene_metrics["gene_support_freq"], errors="coerce").fillna(0.0).clip(lower=0.0, upper=1.0)
        gene_metrics["gene_effect_size"] = np.where(
            sum_weight > 0.0,
            sum_signed / sum_weight,
            0.0,
        )
        gene_metrics["gene_effect_compound"] = (
            gene_metrics["gene_effect_abs_wmean"]
            * gene_metrics["gene_direction_coherence"]
            * np.sqrt(gene_metrics["gene_support_freq"])
        )
        gene_metrics = gene_metrics.drop(columns=["_sum_weight", "_sum_abs_term", "_sum_signed_term"], errors="ignore")

        feature_cols = [group_by]
        feature_metrics = pd.DataFrame(columns=feature_cols)
        if "feature_norm" in work.columns:
            wf = work.copy()
            wf["feature_bucket"] = wf["feature_norm"].astype(str).map(self._biology_feature_bucket)
            wf = wf[wf["feature_bucket"].isin(self._FEATURE_SCORE_ORDER)].copy()
            if not wf.empty:
                fgrp = wf.groupby([group_by, "feature_bucket"], dropna=False).agg(
                    _sum_weight=("bio_weight", "sum"),
                    _sum_abs_term=("abs_term", "sum"),
                    _sum_signed_term=("signed_term", "sum"),
                    _support_freq=("frequency_support", "mean"),
                ).reset_index()
                f_sum_weight = pd.to_numeric(fgrp["_sum_weight"], errors="coerce").fillna(0.0)
                f_sum_abs = pd.to_numeric(fgrp["_sum_abs_term"], errors="coerce").fillna(0.0)
                f_sum_signed = pd.to_numeric(fgrp["_sum_signed_term"], errors="coerce").fillna(0.0)
                f_support = pd.to_numeric(fgrp["_support_freq"], errors="coerce").fillna(0.0).clip(lower=0.0, upper=1.0)
                fgrp["feature_effect_signed_wsum"] = f_sum_signed
                fgrp["feature_direction"] = np.sign(f_sum_signed)
                fgrp["feature_effect_abs_wmean"] = np.where(f_sum_weight > 0.0, f_sum_abs / f_sum_weight, 0.0)
                fgrp["feature_direction_coherence"] = np.where(f_sum_abs > 0.0, np.abs(f_sum_signed) / f_sum_abs, 0.0)
                fgrp["feature_importance"] = (
                    f_sum_abs
                    * fgrp["feature_direction_coherence"]
                    * np.sqrt(f_support)
                )
                pivot_frames: List[pd.DataFrame] = []
                for src_col, out_prefix in (
                    ("feature_importance", "feature_importance"),
                    ("feature_direction", "feature_direction"),
                    ("feature_effect_signed_wsum", "feature_effect_signed_wsum"),
                ):
                    pivot = fgrp.pivot(
                        index=group_by,
                        columns="feature_bucket",
                        values=src_col,
                    ).reset_index()
                    pivot.columns = [
                        c if c == group_by else f"{out_prefix}_{c}"
                        for c in pivot.columns
                    ]
                    pivot_frames.append(pivot)
                feature_metrics = pivot_frames[0]
                for frame in pivot_frames[1:]:
                    feature_metrics = feature_metrics.merge(frame, on=group_by, how="outer")

        return gene_metrics, feature_metrics

    @staticmethod
    def _prune_gene_output_columns(df: pd.DataFrame) -> pd.DataFrame:
        keep_cols = [
            "gene_name",
            "gene_id",
            "unique_dmps",
            "gene_effect_size",
            "gene_direction",
            "gene_effect_signed_wsum",
            "gene_importance",
            "gene_effect_abs_wmean",
            "gene_effect_abs_wsum",
            "gene_direction_coherence",
            "gene_support_n",
            "gene_support_freq",
            "gene_effect_compound",
            "feature_importance_promoter",
            "feature_importance_exon",
            "feature_importance_intron",
            "feature_importance_gene_body",
            "feature_importance_terminator",
            "feature_direction_promoter",
            "feature_direction_exon",
            "feature_direction_intron",
            "feature_direction_gene_body",
            "feature_direction_terminator",
            "feature_effect_signed_wsum_promoter",
            "feature_effect_signed_wsum_exon",
            "feature_effect_signed_wsum_intron",
            "feature_effect_signed_wsum_gene_body",
            "feature_effect_signed_wsum_terminator",
            "hits_promoter",
            "hits_exon",
            "hits_intron",
            "hits_gene_body",
            "hits_terminator",
            "feature_chrom",
            "gene_p_value",
            "gene_q_value",
            "disease_associated",
            "disease_evidence_level",
            "disease_description",
            "disease_source",
            "disease_score",
            "gene_ncbi_link",
            "gene_ensembl_link",
            "gene_uniprot_link",
            "gene_omim_link",
        ]
        return df[[c for c in keep_cols if c in df.columns]].copy()
    
    def aggregate_by_feature(
        self,
        intersect_df: pd.DataFrame,
        group_by: str = 'gene_name'
    ) -> pd.DataFrame:
        """
        Aggregate DMPs by genomic feature.
        
        Args:
            intersect_df: DataFrame with DMP-feature intersections
            group_by: Column to group by ('gene_name', 'transcript_id', 'feature_type', etc.)
            
        Returns:
            Aggregated DataFrame with statistics per feature
        """
        if group_by not in intersect_df.columns:
            raise ValueError(f"Column '{group_by}' not found in intersection DataFrame")
        
        logger.info(f"Aggregating by {group_by}...")
        
        # Build aggregation dictionary using column names as keys
        agg_dict = {}
        
        if 'dmp_name' in intersect_df.columns:
            agg_dict['dmp_name'] = ['count', 'nunique']
        
        if 'weight' in intersect_df.columns:
            agg_dict['weight'] = ['sum', 'mean', 'max']
        
        if 'p_value' in intersect_df.columns:
            agg_dict['p_value'] = ['min', 'mean']
        
        if 'q_value' in intersect_df.columns:
            agg_dict['q_value'] = ['min', 'mean']

        if 'importance' in intersect_df.columns:
            agg_dict['importance'] = ['sum', 'mean', 'max']

        for c in intersect_df.columns:
            cs = str(c)
            if cs.startswith("aux_") and cs.endswith("_n_hits"):
                agg_dict[c] = "sum"
        if "closest_gene_distance_bp" in intersect_df.columns:
            agg_dict["closest_gene_distance_bp"] = "min"
        
        # Perform aggregation
        grouped = intersect_df.groupby(group_by).agg(agg_dict).reset_index()
        
        # Flatten MultiIndex columns
        if isinstance(grouped.columns, pd.MultiIndex):
            new_cols = []
            for col in grouped.columns:
                if col[0] == group_by:
                    new_cols.append(col[0])
                elif col[0] == 'dmp_name':
                    if col[1] == 'count':
                        new_cols.append('dmp_count')
                    elif col[1] == 'nunique':
                        new_cols.append('unique_dmps')
                    else:
                        new_cols.append(f"{col[0]}_{col[1]}")
                elif col[0] == 'weight':
                    if col[1] == 'sum':
                        new_cols.append('total_weight')
                    elif col[1] == 'mean':
                        new_cols.append('mean_weight')
                    elif col[1] == 'max':
                        new_cols.append('max_weight')
                    else:
                        new_cols.append(f"{col[0]}_{col[1]}")
                elif col[0] in ['p_value', 'q_value']:
                    if col[1] == 'min':
                        new_cols.append(f"min_{col[0]}")
                    elif col[1] == 'mean':
                        new_cols.append(f"mean_{col[0]}")
                    else:
                        new_cols.append(f"{col[0]}_{col[1]}")
                elif col[0] in ['effect_size', 'delta_mean']:
                    if col[1] == 'mean':
                        new_cols.append(f"mean_{col[0]}")
                    elif col[1] == 'max':
                        new_cols.append(f"max_{col[0]}")
                    else:
                        new_cols.append(f"{col[0]}_{col[1]}")
                elif col[0] == 'importance':
                    if col[1] == 'sum':
                        new_cols.append('total_importance')
                    elif col[1] == 'mean':
                        new_cols.append('mean_importance')
                    elif col[1] == 'max':
                        new_cols.append('max_importance')
                    else:
                        new_cols.append(f"{col[0]}_{col[1]}")
                else:
                    new_cols.append(f"{col[0]}_{col[1]}" if col[1] else col[0])
            grouped.columns = new_cols
        else:
            # Already flattened
            grouped.columns = [col[0] if isinstance(col, tuple) and len(col) == 2 else col for col in grouped.columns]

        if group_by in ("gene_name", "gene_id"):
            hits_df = self._compute_exclusive_feature_hits(intersect_df, group_by=group_by)
            if not hits_df.empty:
                grouped = grouped.merge(hits_df, on=group_by, how="left")
            for col in ("hits_promoter", "hits_exon", "hits_intron", "hits_gene_body", "hits_terminator"):
                if col not in grouped.columns:
                    grouped[col] = 0
                grouped[col] = pd.to_numeric(grouped[col], errors="coerce").fillna(0).astype(int)
        
        # Add Stouffer aggregated gene p-values (weighted, signed by delta_mean)
        if 'p_value' in intersect_df.columns:
            from scipy.stats import norm

            def _compute_gene_pvalue(group: pd.DataFrame) -> pd.Series:
                pvals = group['p_value'].astype(float).to_numpy()
                if 'combined_weight' in group.columns:
                    weights = group['combined_weight'].astype(float).to_numpy()
                elif 'weight' in group.columns:
                    weights = group['weight'].astype(float).to_numpy()
                else:
                    weights = np.ones_like(pvals)
                if 'delta_mean' in group.columns:
                    signs = np.sign(group['delta_mean'].astype(float).to_numpy())
                elif 'direction' in group.columns:
                    signs = self._direction_to_sign(group['direction']).to_numpy(dtype=float)
                else:
                    signs = np.ones_like(pvals)

                valid = np.isfinite(pvals) & np.isfinite(weights)
                if 'delta_mean' in group.columns or 'direction' in group.columns:
                    valid &= np.isfinite(signs)

                pvals = pvals[valid]
                weights = weights[valid]
                signs = signs[valid]

                if len(pvals) == 0:
                    return pd.Series({
                        "gene_p_value": np.nan,
                        "gene_z": np.nan,
                        "gene_direction": np.nan,
                        "gene_weight_sumsq": np.nan,
                        "gene_z_numerator": np.nan,
                    })

                pvals = np.clip(pvals, 1e-300, 1.0 - 1e-16)
                z_scores = norm.ppf(1 - pvals / 2.0)
                z_scores = np.clip(z_scores, -8.0, 8.0)
                signed_z = z_scores * signs

                weight_sumsq = float(np.sum(weights ** 2))
                if not np.isfinite(weight_sumsq) or weight_sumsq <= 0:
                    return pd.Series({
                        "gene_p_value": np.nan,
                        "gene_z": np.nan,
                        "gene_direction": np.nan,
                        "gene_weight_sumsq": np.nan,
                        "gene_z_numerator": np.nan,
                    })

                z_numerator = float(np.sum(weights * signed_z))
                combined_z = float(z_numerator / np.sqrt(weight_sumsq))
                gene_p = float(2 * (1 - norm.cdf(abs(combined_z))))
                gene_p = float(np.clip(gene_p, 0.0, 1.0))

                return pd.Series({
                    "gene_p_value": gene_p,
                    "gene_z": combined_z,
                    "gene_direction": float(np.sign(combined_z)),
                    "gene_weight_sumsq": weight_sumsq,
                    "gene_z_numerator": z_numerator,
                })

            try:
                gene_stats = intersect_df.groupby(group_by).apply(_compute_gene_pvalue, include_groups=False).reset_index()
            except TypeError:
                gene_stats = intersect_df.groupby(group_by).apply(_compute_gene_pvalue).reset_index()
            grouped = grouped.merge(gene_stats, on=group_by, how='left')

            gene_pvals = grouped["gene_p_value"].to_numpy(dtype=float)
            gene_qvals = np.full_like(gene_pvals, np.nan, dtype=float)
            finite_mask = np.isfinite(gene_pvals)
            if np.any(finite_mask):
                kwargs = {}
                if getattr(self, 'storey_lambda', None) is not None:
                    kwargs['lambdas'] = np.array([self.storey_lambda], dtype=float)
                _qvals, _ = storey_qvalues(gene_pvals[finite_mask], **kwargs)
                gene_qvals[finite_mask] = _qvals
            grouped["gene_q_value"] = gene_qvals

        # Keep gene-level stat columns stable across exports, even when not computable.
        for col in ["gene_p_value", "gene_q_value", "gene_z", "gene_direction", "gene_weight_sumsq", "gene_z_numerator"]:
            if col not in grouped.columns:
                grouped[col] = np.nan

        # Fallback: when p-values were not available for Stouffer, expose a proxy gene_p_value from group-level minima.
        gene_p_numeric = pd.to_numeric(grouped["gene_p_value"], errors="coerce")
        if not np.isfinite(gene_p_numeric).any():
            for fallback_col in ("min_p_value", "mean_p_value", "min_q_value", "mean_q_value"):
                if fallback_col in grouped.columns:
                    fallback_vals = pd.to_numeric(grouped[fallback_col], errors="coerce")
                    fallback_vals = fallback_vals.where(np.isfinite(fallback_vals), np.nan)
                    if np.isfinite(fallback_vals).any():
                        grouped["gene_p_value"] = fallback_vals.astype(float)
                        break

        # If gene_q_value is still missing but gene_p_value is available, derive q-values from gene_p_value.
        gene_q_numeric = pd.to_numeric(grouped["gene_q_value"], errors="coerce")
        gene_p_numeric = pd.to_numeric(grouped["gene_p_value"], errors="coerce")
        if not np.isfinite(gene_q_numeric).any() and np.isfinite(gene_p_numeric).any():
            gene_qvals = np.full(len(grouped), np.nan, dtype=float)
            finite_mask = np.isfinite(gene_p_numeric.to_numpy(dtype=float))
            if np.any(finite_mask):
                kwargs = {}
                if getattr(self, "storey_lambda", None) is not None:
                    kwargs["lambdas"] = np.array([self.storey_lambda], dtype=float)
                try:
                    _qvals, _ = storey_qvalues(gene_p_numeric.to_numpy(dtype=float)[finite_mask], **kwargs)
                    gene_qvals[finite_mask] = _qvals
                    grouped["gene_q_value"] = gene_qvals
                except Exception as exc:
                    logger.warning("Failed to derive gene_q_value from gene_p_value fallback: %s", exc)

        # Fallback: fill undefined stats (NaN) when the gene has DMPs (dmp_count > 0) by recomputing from finite values in the intersection table
        stat_aggs = []
        if 'p_value' in intersect_df.columns:
            stat_aggs.append(('p_value', 'min', 'min_p_value'))
            stat_aggs.append(('p_value', 'mean', 'mean_p_value'))
        if 'q_value' in intersect_df.columns:
            stat_aggs.append(('q_value', 'min', 'min_q_value'))
            stat_aggs.append(('q_value', 'mean', 'mean_q_value'))
        if stat_aggs:
            for src_col, agg_name, out_col in stat_aggs:
                if out_col not in grouped.columns:
                    continue
                nan_mask = grouped[out_col].isna() & (grouped.get('dmp_count', 0) > 0)
                if not nan_mask.any():
                    continue
                # Recompute from intersect_df using only finite values per group (avoids All-NaN / empty-slice warnings)
                def _safe_reduce(arr, kind):
                    a = np.ravel(arr).astype(float)
                    finite = a[np.isfinite(a)]
                    if finite.size == 0:
                        return np.nan
                    if kind == 'min':
                        return np.min(finite)
                    if kind == 'max':
                        return np.max(finite)
                    return np.mean(finite)

                fallback = (
                    intersect_df.groupby(group_by)[src_col]
                    .apply(lambda x: _safe_reduce(x, agg_name))
                    .reset_index()
                )
                fallback.columns = [group_by, out_col + '_fb']
                grouped = grouped.merge(fallback, on=group_by, how='left')
                grouped[out_col] = grouped[out_col].fillna(grouped[out_col + '_fb'])
                grouped = grouped.drop(columns=[out_col + '_fb'], errors='ignore')

        # Add feature metadata (take first occurrence)
        metadata_cols = ['feature_chrom', 'gene_id']
        available_metadata = [c for c in metadata_cols if c in intersect_df.columns]
        if available_metadata:
            metadata = intersect_df.groupby(group_by)[available_metadata].first().reset_index()
            grouped = grouped.merge(metadata, on=group_by, how='left')

        # Biology-weighted compound importance (canonical path).
        score_source_df = self._exclusive_feature_rows(intersect_df, group_by=group_by)
        if score_source_df.empty:
            score_source_df = intersect_df
        if "effect_size" in score_source_df.columns:
            is_stability_like = {"count", "n_runs"}.issubset(set(score_source_df.columns))
            if is_stability_like and "frequency" not in score_source_df.columns:
                raise ValueError(
                    "Stability/fixed-panel input requires 'frequency' for biological importance, "
                    "but the column is missing."
                )
            if "frequency" in score_source_df.columns and is_stability_like:
                freq = pd.to_numeric(score_source_df["frequency"], errors="coerce")
                invalid = (~np.isfinite(freq)) | (freq < 0.0) | (freq > 1.0)
                if invalid.any():
                    bad_cols = [c for c in [group_by, "dmp_name", "frequency"] if c in score_source_df.columns]
                    bad = score_source_df.loc[invalid, bad_cols].head(5).to_dict(orient="records")
                    raise ValueError(
                        "Invalid stability frequency values for biological importance "
                        "(expected finite values in [0,1]). "
                        f"Examples: {bad}"
                    )

        gene_compound_df, feature_compound_df = self._build_compound_effect_metrics(
            score_source_df=score_source_df,
            group_by=group_by,
        )
        if not gene_compound_df.empty:
            grouped = grouped.merge(gene_compound_df, on=group_by, how="left")
            if "gene_direction_x" in grouped.columns or "gene_direction_y" in grouped.columns:
                grouped["gene_direction"] = pd.to_numeric(
                    grouped.get("gene_direction_y", grouped.get("gene_direction_x")),
                    errors="coerce",
                )
                grouped = grouped.drop(columns=["gene_direction_x", "gene_direction_y"], errors="ignore")
        if not feature_compound_df.empty:
            grouped = grouped.merge(feature_compound_df, on=group_by, how="left")
        for col in (
            "gene_effect_size",
            "gene_direction",
            "gene_effect_abs_wmean",
            "gene_effect_abs_wsum",
            "gene_effect_signed_wsum",
            "gene_direction_coherence",
            "gene_support_freq",
            "gene_effect_compound",
        ):
            if col not in grouped.columns:
                grouped[col] = 0.0
            grouped[col] = pd.to_numeric(grouped[col], errors="coerce").fillna(0.0)
        for feature in self._FEATURE_SCORE_ORDER:
            for col in (
                f"feature_importance_{feature}",
                f"feature_direction_{feature}",
                f"feature_effect_signed_wsum_{feature}",
            ):
                if col not in grouped.columns:
                    grouped[col] = 0.0
                grouped[col] = pd.to_numeric(grouped[col], errors="coerce").fillna(0.0)
        if "gene_support_n" not in grouped.columns:
            grouped["gene_support_n"] = 0
        grouped["gene_support_n"] = pd.to_numeric(grouped["gene_support_n"], errors="coerce").fillna(0).astype(int)

        # Canonical importance: biology-weighted burden × coherence × sqrt(support).
        grouped["gene_importance"] = (
            pd.to_numeric(grouped.get("gene_effect_abs_wsum"), errors="coerce").fillna(0.0)
            * pd.to_numeric(grouped.get("gene_direction_coherence"), errors="coerce").fillna(0.0)
            * np.sqrt(pd.to_numeric(grouped.get("gene_support_freq"), errors="coerce").fillna(0.0).clip(lower=0.0))
        )

        sort_cols = ["gene_importance"]
        if "unique_dmps" in grouped.columns:
            sort_cols.append("unique_dmps")
        if "gene_effect_abs_wsum" in grouped.columns:
            sort_cols.append("gene_effect_abs_wsum")
        grouped = grouped.sort_values(sort_cols, ascending=False).reset_index(drop=True)

        stat_check_cols = [c for c in ["min_p_value", "gene_effect_abs_wsum"] if c in grouped.columns]
        support_col = None
        if "unique_dmps" in grouped.columns:
            support_col = "unique_dmps"
        elif "gene_support_n" in grouped.columns:
            support_col = "gene_support_n"
        elif "dmp_count" in grouped.columns:
            support_col = "dmp_count"
        if stat_check_cols and support_col is not None:
            has_dmps = grouped[support_col].fillna(0) > 0
            all_stats_missing = pd.Series(True, index=grouped.index)
            for c in stat_check_cols:
                all_stats_missing = all_stats_missing & grouped[c].isna()
            genes_missing = has_dmps & all_stats_missing
            n_genes_missing = genes_missing.sum()
            if n_genes_missing > 0:
                examples = grouped.loc[genes_missing, group_by].head(3).tolist()
                logger.warning(
                    "%d %s(s) have DMPs but missing p_value/effect burden (e.g. %s). "
                    "Check that DMP CSV column names and chromosome/position/context match the BED.",
                    n_genes_missing, group_by, ", ".join(str(g) for g in examples)
                )

        logger.info(f"Aggregated into {len(grouped)} {group_by}s")
        return grouped

    def _should_enrich_gene_results(self, group_by: str) -> bool:
        """Return True when disease enrichment should be applied to grouped gene outputs."""
        return bool(
            self.enrich_disease
            and self.disease_enricher is not None
            and group_by in ['gene_name', 'gene_id']
        )

    def _build_shared_enrichment_payload(
        self,
        aggregated_frames: List[pd.DataFrame],
        group_by: str,
    ) -> Optional[Dict]:
        """Build one enrichment payload for the union of genes across multiple result frames."""
        if not self._should_enrich_gene_results(group_by):
            return None

        genes: List[str] = []
        for frame in aggregated_frames:
            if frame.empty or group_by not in frame.columns:
                continue
            genes.extend(frame[group_by].dropna().astype(str).tolist())

        if not genes:
            return None

        return self.disease_enricher._build_enrichment_payload(
            genes,
            disease_term=self.disease_enricher.disease_term,
        )

    def _apply_shared_enrichment_payload(
        self,
        df: pd.DataFrame,
        group_by: str,
        payload: Optional[Dict],
        separate_sources: bool = False,
    ):
        """Apply a precomputed enrichment payload to a grouped result frame."""
        if payload is None or not self._should_enrich_gene_results(group_by):
            return df

        return self.disease_enricher._apply_enrichment_payload(
            df,
            gene_column=group_by,
            payload=payload,
            separate_sources=separate_sources,
        )

    def _find_stable_k_by_gene_count(
        self,
        dmp_df: pd.DataFrame,
        group_by: str,
        min_k: int,
        max_k: int,
    ) -> Tuple[int, pd.DataFrame, Dict]:
        """
        Phase 1: Find k where the total number of unique genes stabilizes (no API calls).
        Stability = no new genes for stability_threshold consecutive k values.
        Returns (k_stable, aggregated_gene_df_at_k_stable, phase1_log).
        """
        optimization_log: Dict = {
            'phase1_k_values': [],
            'phase1_gene_counts': [],
            'k_stable': None,
        }
        prev_genes: set = set()
        consecutive_stable = 0
        k_test = min_k
        aggregated_stable = pd.DataFrame()
        k_stable = max_k

        while k_test <= max_k:
            logger.info(f"Phase 1 (stabilize): testing k={k_test}")
            dmp_subset = dmp_df.head(k_test).copy()
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_csv = Path(temp_dir) / "temp.csv"
                dmp_subset.to_csv(temp_csv, index=False)
                bed_file = self.csv_to_bed(temp_csv, Path(temp_dir) / "temp.bed")
                intersect_df = self.intersect_with_features(bed_file, dmp_subset)
            if intersect_df.empty:
                logger.warning(f"No features found for k={k_test}")
                k_test += max(1, (max_k - min_k) // 20)
                continue
            aggregated = self.aggregate_by_feature(intersect_df, group_by=group_by)
            current_genes = set(aggregated[group_by].dropna().astype(str).unique())
            new_genes = current_genes - prev_genes
            optimization_log['phase1_k_values'].append(k_test)
            optimization_log['phase1_gene_counts'].append(len(current_genes))
            if len(new_genes) == 0:
                consecutive_stable += 1
                logger.info(f"  No new genes (stable for {consecutive_stable}/{self.stability_threshold})")
            else:
                consecutive_stable = 0
                logger.info(f"  {len(current_genes)} genes ({len(new_genes)} new)")
            if consecutive_stable >= self.stability_threshold:
                k_stable = k_test - (self.stability_threshold - 1)
                k_stable = max(min_k, k_stable)
                dmp_subset_stable = dmp_df.head(k_stable).copy()
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_csv = Path(temp_dir) / "temp.csv"
                    dmp_subset_stable.to_csv(temp_csv, index=False)
                    bed_file = self.csv_to_bed(temp_csv, Path(temp_dir) / "temp.bed")
                    intersect_df = self.intersect_with_features(bed_file, dmp_subset_stable)
                if not intersect_df.empty:
                    aggregated_stable = self.aggregate_by_feature(intersect_df, group_by=group_by)
                else:
                    aggregated_stable = aggregated.copy()
                optimization_log['k_stable'] = k_stable
                logger.info(f"Phase 1 complete: k_stable={k_stable}, {len(aggregated_stable)} genes")
                return k_stable, aggregated_stable, optimization_log
            prev_genes = current_genes
            step = max(1, (max_k - min_k) // 20) if consecutive_stable == 0 else max(1, (max_k - min_k) // 50)
            k_test += step

        k_stable = min(k_test - 1, max_k) if k_test > min_k else max_k
        optimization_log['k_stable'] = k_stable
        dmp_subset = dmp_df.head(k_stable).copy()
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_csv = Path(temp_dir) / "temp.csv"
            dmp_subset.to_csv(temp_csv, index=False)
            bed_file = self.csv_to_bed(temp_csv, Path(temp_dir) / "temp.bed")
            intersect_df = self.intersect_with_features(bed_file, dmp_subset)
        if not intersect_df.empty:
            aggregated_stable = self.aggregate_by_feature(intersect_df, group_by=group_by)
        else:
            aggregated_stable = pd.DataFrame()
        logger.warning(f"Phase 1: no stable k found, using k={k_stable}")
        return k_stable, aggregated_stable, optimization_log

    def optimize_dmps_for_stable_genes(
        self,
        dmp_df: pd.DataFrame,
        group_by: str = 'gene_name',
        min_k: Optional[int] = None,
        max_k: Optional[int] = None
    ) -> Tuple[int, pd.DataFrame, Dict]:
        """
        Three-phase DMP optimization: (1) stabilize on gene count (no API),
        (2) enrich once (Grok annotation + Open Targets evidence),
        (3) optional extension loop adding DMPs and enriching only new genes until no new disease gene.
        """
        if group_by not in ['gene_name', 'gene_id']:
            raise ValueError(f"optimize_dmps_for_stable_genes requires group_by='gene_name' or 'gene_id', got '{group_by}'")
        min_k = min_k or self.min_k
        max_k = max_k or self.max_k or len(dmp_df)
        max_k = min(max_k, len(dmp_df))
        dmp_df = self._sort_dmps_for_optimization(dmp_df)
        if min_k >= max_k:
            logger.warning(f"min_k ({min_k}) >= max_k ({max_k}), using all DMPs")
            return max_k, pd.DataFrame(), {'optimal_k': max_k}

        # Phase 1: stabilize by gene count (no enricher)
        k_stable, aggregated_stable, phase1_log = self._find_stable_k_by_gene_count(dmp_df, group_by, min_k, max_k)
        optimization_log = dict(phase1_log)
        optimization_log['optimal_k'] = k_stable
        optimization_log['phase2_enriched'] = False
        optimization_log['phase3_extended'] = False
        optimization_log['phase3_steps'] = []
        if aggregated_stable.empty:
            return k_stable, aggregated_stable, optimization_log

        # If enrichment disabled, return stabilized set without disease columns
        if not self.enrich_disease or not self.disease_enricher:
            logger.info("Enrichment disabled; returning Phase 1 gene set without disease columns.")
            return k_stable, aggregated_stable, optimization_log

        # Phase 2: enrich once (both Grok and Open Targets via enrich_gene_dataframe)
        logger.info("Phase 2: Enriching stabilized gene set (Grok annotation + Open Targets evidence)...")
        optimal_gene_df = self.disease_enricher.enrich_gene_dataframe(
            aggregated_stable.copy(),
            gene_column=group_by
        )
        optimization_log['phase2_enriched'] = True
        genes_at_k_stable = set(optimal_gene_df[group_by].dropna().astype(str).unique())

        # Phase 3: optional extension when last gene is strongly disease-associated
        def _last_gene_strongly_associated(df: pd.DataFrame) -> bool:
            if df.empty or 'disease_associated' not in df.columns:
                return False
            sort_col = "gene_importance"
            if sort_col not in df.columns:
                return bool(df["disease_associated"].iloc[-1])
            last_row = df.sort_values(sort_col, ascending=True).iloc[0]
            if not last_row.get('disease_associated', False):
                return False
            ev = str(last_row.get('disease_evidence_level', '')).lower()
            return ev in ('high', 'medium', 'low')

        if self.extend_after_stable and _last_gene_strongly_associated(optimal_gene_df):
            logger.info("Phase 3: Last gene strongly disease-associated; running extension loop...")
            k_ext = k_stable
            known_genes = set(genes_at_k_stable)
            pending_new_genes: set = set()
            pending_stats_frames: List[pd.DataFrame] = []
            phase3_batch_size = max(1, getattr(self.disease_enricher, 'grok_batch_size', 16))
            while k_ext < len(dmp_df):
                k_ext += 1
                dmp_subset = dmp_df.head(k_ext).copy()
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_csv = Path(temp_dir) / "temp.csv"
                    dmp_subset.to_csv(temp_csv, index=False)
                    bed_file = self.csv_to_bed(temp_csv, Path(temp_dir) / "temp.bed")
                    intersect_df = self.intersect_with_features(bed_file, dmp_subset)
                if intersect_df.empty:
                    continue
                agg_k = self.aggregate_by_feature(intersect_df, group_by=group_by)
                genes_at_k = set(agg_k[group_by].dropna().astype(str).unique())
                new_genes = genes_at_k - known_genes
                if not new_genes:
                    continue
                known_genes.update(new_genes)
                pending_new_genes.update(new_genes)
                pending_stats_frames.append(
                    agg_k[agg_k[group_by].astype(str).isin(new_genes)].copy()
                )
                if len(pending_new_genes) < phase3_batch_size and k_ext < len(dmp_df):
                    optimization_log['phase3_steps'].append({
                        'k': k_ext,
                        'new_genes': len(new_genes),
                        'batched_genes': len(pending_new_genes),
                        'new_disease_genes': None,
                    })
                    continue

                new_df = pd.DataFrame({group_by: sorted(pending_new_genes)})
                enriched_new = self.disease_enricher.enrich_gene_dataframe(new_df, gene_column=group_by)
                disease_new = set()
                if 'disease_associated' in enriched_new.columns:
                    disease_new = set(
                        enriched_new.loc[enriched_new['disease_associated'] == True, group_by]
                        .dropna().astype(str).unique()
                    )
                optimization_log['phase3_steps'].append({
                    'k': k_ext,
                    'new_genes': len(new_genes),
                    'batched_genes': len(pending_new_genes),
                    'new_disease_genes': len(disease_new),
                })
                if not disease_new:
                    logger.info(f"Phase 3: k={k_ext}, no new disease-associated genes; stopping.")
                    break
                stats_pending = pd.concat(pending_stats_frames, ignore_index=True)
                stats_new = stats_pending[stats_pending[group_by].astype(str).isin(disease_new)]
                enricher_cols = [c for c in enriched_new.columns if c != group_by]
                merged_new = stats_new.merge(
                    enriched_new[[group_by] + enricher_cols],
                    on=group_by,
                    how='left'
                )
                optimal_gene_df = pd.concat([optimal_gene_df, merged_new], ignore_index=True)
                genes_at_k_stable = genes_at_k
                pending_new_genes.clear()
                pending_stats_frames = []
                k_stable = k_ext
                logger.info(f"Phase 3: k={k_ext}, added {len(disease_new)} disease genes; continuing.")
            optimization_log['phase3_extended'] = True
            optimization_log['optimal_k'] = k_stable
        else:
            if not self.extend_after_stable:
                logger.info("Phase 3: disabled (extend_after_stable=False).")
            else:
                logger.info("Phase 3: skipped (last gene not strongly disease-associated).")

        optimization_log['optimal_k'] = k_stable
        # Backward compatibility: old log shape had k_values, disease_related_counts, etc.
        optimization_log.setdefault('k_values', optimization_log.get('phase1_k_values', []))
        optimization_log.setdefault('disease_related_counts', [])
        optimization_log.setdefault('unrelated_counts', [])
        optimization_log.setdefault('disease_related_genes', [])
        optimization_log.setdefault('unrelated_growth_rates', [])
        logger.info(f"Optimization complete: optimal k={k_stable}")
        return k_stable, optimal_gene_df, optimization_log

    def _map_all_contexts_per_chromosome(
        self,
        csv_files: List[Path],
        output_dir: Path,
        group_by: str,
    ) -> Dict[str, pd.DataFrame]:
        """
        Load all DMPs from the given CSV files, group by chromosome, and run
        mapping once per chromosome (all contexts combined), matching spMapDMP2Genes.
        """
        all_dmps = []
        for csv_file in csv_files:
            df = pd.read_csv(csv_file)
            df = self._normalize_dmp_columns(df)
            all_dmps.append(df)
        combined = pd.concat(all_dmps, ignore_index=True)
        if 'chromosome' not in combined.columns:
            raise ValueError("DMP DataFrames must have a 'chromosome' column to process by chromosome")
        if "position" in combined.columns:
            combined["position"] = (
                pd.to_numeric(combined["position"], errors="coerce").fillna(0).astype(np.uint32)
            )
        combined['chromosome'] = combined['chromosome'].astype(str).str.strip()
        chromosomes = sorted(combined['chromosome'].unique(), key=lambda c: (c.replace('chr', '').isdigit(), c.replace('chr', '') or '0', c))
        logger.info(f"Processing {len(chromosomes)} chromosome(s) with all contexts combined: {chromosomes}")

        results = {}
        result_artifacts = []

        with tempfile.TemporaryDirectory() as temp_dir:
            for chrom in chromosomes:
                dmp_df = combined[combined['chromosome'] == chrom].copy()
                dmp_df = self._sort_dmps_for_optimization(dmp_df)
                chrom_safe = str(chrom).replace('chr', '') or chrom
                logger.info(f"\n{'='*70}\nChromosome {chrom} ({len(dmp_df)} DMPs, all contexts)\n{'='*70}")

                try:
                    temp_csv = Path(temp_dir) / f"chr{chrom_safe}.csv"
                    dmp_df.to_csv(temp_csv, index=False)
                    bed_file = Path(temp_dir) / f"chr{chrom_safe}.bed"
                    self.csv_to_bed(temp_csv, bed_file)
                    intersect_df = self.intersect_with_features(bed_file, dmp_df)

                    if intersect_df.empty:
                        logger.warning(f"No features found for chromosome {chrom}")
                        results[f"chr{chrom_safe}"] = pd.DataFrame()
                        continue

                    aggregated = self.aggregate_by_feature(intersect_df, group_by=group_by)
                    needs_shared_enrichment = self._should_enrich_gene_results(group_by)

                    output_csv = output_dir / f"chr{chrom_safe}-features-{group_by}.csv"
                    detail_csv = output_dir / f"chr{chrom_safe}-intersections.csv"

                    result_artifacts.append({
                        'key': f"chr{chrom_safe}",
                        'chromosome': chrom,
                        'aggregated': aggregated,
                        'intersect_df': intersect_df,
                        'output_csv': output_csv,
                        'detail_csv': detail_csv,
                        'needs_shared_enrichment': needs_shared_enrichment,
                    })
                    results[f"chr{chrom_safe}"] = aggregated
                    logger.info(f"   Mapped {len(aggregated)} unique {group_by}s for chromosome {chrom}")
                except Exception as e:
                    logger.error(f"Failed chromosome {chrom}: {e}")
                    import traceback
                    traceback.print_exc()
                    results[f"chr{chrom_safe}"] = pd.DataFrame()

        shared_payload = self._build_shared_enrichment_payload(
            [a['aggregated'] for a in result_artifacts if a['needs_shared_enrichment']],
            group_by=group_by,
        )

        for artifact in result_artifacts:
            agg = artifact['aggregated']
            if artifact['needs_shared_enrichment']:
                agg = self._apply_shared_enrichment_payload(
                    agg, group_by=group_by, payload=shared_payload, separate_sources=False,
                )
            if group_by in ("gene_name", "gene_id"):
                agg = self._prune_gene_output_columns(agg)
            agg.to_csv(artifact['output_csv'], index=False)
            artifact['intersect_df'].to_csv(artifact['detail_csv'], index=False)
            results[artifact['key']] = agg
            logger.info(f"   Saved {artifact['output_csv']}")

        if len(results) > 1 and any(not df.empty for df in results.values()):
            non_empty = [results[k] for k in results if not results[k].empty]
            combined_df = pd.concat(non_empty, ignore_index=True)
            combined_csv = output_dir / f"all-{group_by}-combined.csv"
            combined_df.to_csv(combined_csv, index=False)
            logger.info(f"   Combined results saved to {combined_csv} (concatenated; enrichment preserved)")

        return results

    def map_csv_files(
        self,
        csv_pattern: str,
        output_dir: Optional[Path] = None,
        group_by: str = 'gene_name',
        process_all_contexts_per_chromosome: bool = True,
    ) -> Dict[str, pd.DataFrame]:
        """
        Map multiple CSV files matching a pattern.
        When process_all_contexts_per_chromosome is True (default), loads all DMPs
        from all matching files, groups by chromosome, and runs mapping once per
        chromosome (all contexts combined), matching spMapDMP2Genes behavior.

        Args:
            csv_pattern: Glob pattern for CSV files (e.g., "dmps-*.csv")
                        Can be absolute path or relative to current directory
            output_dir: Output directory for results. If None, uses CSV directory.
            group_by: Feature to group by ('gene_name', 'transcript_id', etc.)
            process_all_contexts_per_chromosome: If True, load all CSVs, group by
                        chromosome, and run one mapping per chromosome (all contexts).
                        If False, process each CSV file separately.

        Returns:
            Dictionary mapping CSV file path or chromosome key to aggregated results DataFrames
        """
        csv_path = Path(csv_pattern)

        # Support path-level wildcards such as /path/to/detections/*/*/dmps-*.csv for comparison dirs
        path_wildcard = "/*/"
        if path_wildcard in csv_pattern:
            base_str, _, rest = csv_pattern.partition(path_wildcard)
            search_dir = Path(base_str)
            pattern = "*/" + rest  # e.g. "*/dmps-*.csv" so search_dir.glob finds all group subdirs
        elif csv_path.is_absolute():
            search_dir = csv_path.parent
            pattern = csv_path.name
        else:
            if csv_path.exists() and csv_path.is_dir():
                search_dir = csv_path
                pattern = "*.csv"
            else:
                search_dir = Path.cwd()
                pattern = csv_pattern

        # Find matching CSV files
        csv_files = sorted(search_dir.glob(pattern))
        if not csv_files:
            csv_files = glob_discovery_dmps_with_unified_fallback(search_dir, pattern)
            if csv_files:
                logger.info(
                    "No files matched %r; using unified / non-suffixed DMP exports (%d file(s))",
                    pattern,
                    len(csv_files),
                )

        if not csv_files:
            raise FileNotFoundError(f"No CSV files found matching pattern: {csv_pattern} (searched in {search_dir})")

        logger.info(f"Found {len(csv_files)} CSV files matching pattern: {csv_pattern}")

        if output_dir is None:
            output_dir = search_dir / "mapped_features"
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        if process_all_contexts_per_chromosome and len(csv_files) > 0:
            return self._map_all_contexts_per_chromosome(
                csv_files=csv_files,
                output_dir=output_dir,
                group_by=group_by,
            )

        results = {}
        result_artifacts = []

        with tempfile.TemporaryDirectory() as temp_dir:
            for csv_file in csv_files:
                logger.info(f"\n{'='*70}")
                logger.info(f"Processing: {csv_file.name}")
                logger.info(f"{'='*70}")
                
                try:
                    # Load DMPs and normalize column names (p-value -> p_value, Effect Size -> effect_size, etc.)
                    dmp_df = pd.read_csv(csv_file)
                    dmp_df = self._normalize_dmp_columns(dmp_df)
                    dmp_df_sorted = self._sort_dmps_for_optimization(dmp_df)
                    needs_shared_enrichment = False
                    
                    # Apply DMP optimization if enabled
                    optimal_k = None
                    if self.optimize_dmps and self.enrich_disease and self.disease_enricher and group_by in ['gene_name', 'gene_id']:
                        logger.info(f"Optimizing DMP count for stable gene sets...")
                        optimal_k, optimal_gene_df, optimization_log = self.optimize_dmps_for_stable_genes(
                            dmp_df_sorted,
                            group_by=group_by
                        )
                        
                        # Export optimization log
                        import json
                        log_file = output_dir / f"{csv_file.stem}-optimization-log.json"
                        with open(log_file, 'w') as f:
                            json.dump(optimization_log, f, indent=2)
                        logger.info(f"   Optimization log saved to: {log_file}")
                        
                        # Export optimized DMP subset
                        optimized_dmp_df = dmp_df_sorted.head(optimal_k).copy()
                        optimized_dmp_csv = output_dir / f"{csv_file.stem}-optimized-k{optimal_k}.csv"
                        optimized_dmp_df.to_csv(optimized_dmp_csv, index=False)
                        logger.info(f"   Optimized DMP subset (k={optimal_k}) saved to: {optimized_dmp_csv}")
                        
                        # Use optimized gene set
                        aggregated = optimal_gene_df
                        
                        # Convert optimized DMPs to BED for detailed intersections
                        bed_file = Path(temp_dir) / f"{csv_file.stem}.bed"
                        self.csv_to_bed(optimized_dmp_csv, bed_file)
                        intersect_df = self.intersect_with_features(bed_file, optimized_dmp_df)
                    else:
                        # Original behavior: use all DMPs
                        # Convert to BED
                        bed_file = Path(temp_dir) / f"{csv_file.stem}.bed"
                        self.csv_to_bed(csv_file, bed_file)
                        
                        # Intersect with features
                        intersect_df = self.intersect_with_features(bed_file, dmp_df)
                        
                        if intersect_df.empty:
                            logger.warning(f"No features found for {csv_file.name}")
                            continue
                        
                        # Aggregate by feature
                        aggregated = self.aggregate_by_feature(intersect_df, group_by=group_by)
                        
                        # Defer enrichment so all per-file outputs can share one payload and cache pass
                        needs_shared_enrichment = self._should_enrich_gene_results(group_by)
                        if needs_shared_enrichment:
                            logger.info(f"Deferring disease enrichment for {group_by} until the shared gene payload is built...")
                        elif self.enrich_disease and group_by in ['gene_name', 'gene_id'] and self.disease_enricher is None:
                            logger.warning("Disease enrichment was requested but enricher is not available (init failed). Output will not include disease columns.")
                    
                    output_csv = output_dir / f"{csv_file.stem}-features-{group_by}.csv"
                    if self.optimize_dmps and self.enrich_disease and self.disease_enricher and group_by in ['gene_name', 'gene_id']:
                        output_csv = output_dir / f"{csv_file.stem}-optimized-k{optimal_k}-features-{group_by}.csv"
                    
                    detail_csv = output_dir / f"{csv_file.stem}-intersections.csv"
                    if self.optimize_dmps and self.enrich_disease and self.disease_enricher and group_by in ['gene_name', 'gene_id']:
                        detail_csv = output_dir / f"{csv_file.stem}-optimized-k{optimal_k}-intersections.csv"
                    
                    result_artifacts.append(
                        {
                            'csv_file': str(csv_file),
                            'aggregated': aggregated,
                            'intersect_df': intersect_df,
                            'output_csv': output_csv,
                            'detail_csv': detail_csv,
                            'needs_shared_enrichment': needs_shared_enrichment,
                        }
                    )
                    
                    logger.info(f"✅ Mapped {len(intersect_df)} DMP-feature pairs")
                    logger.info(f"   Found {len(aggregated)} unique {group_by}s")
                    
                except Exception as e:
                    logger.error(f"Failed to process {csv_file.name}: {e}")
                    import traceback
                    traceback.print_exc()
                    continue

        shared_payload = self._build_shared_enrichment_payload(
            [artifact['aggregated'] for artifact in result_artifacts if artifact['needs_shared_enrichment']],
            group_by=group_by,
        )

        for artifact in result_artifacts:
            aggregated = artifact['aggregated']
            if artifact['needs_shared_enrichment']:
                aggregated = self._apply_shared_enrichment_payload(
                    aggregated,
                    group_by=group_by,
                    payload=shared_payload,
                    separate_sources=False,
                )
            if group_by in ("gene_name", "gene_id"):
                aggregated = self._prune_gene_output_columns(aggregated)

            aggregated.to_csv(artifact['output_csv'], index=False)
            artifact['intersect_df'].to_csv(artifact['detail_csv'], index=False)
            results[artifact['csv_file']] = aggregated

            logger.info(f"   Results saved to: {artifact['output_csv']}")
            logger.info(f"   Detailed intersections: {artifact['detail_csv']}")
        
        # Combine all results if multiple files: concatenate per-file CSVs so enrichment (Grok/Open Targets) is preserved
        if len(results) > 1:
            logger.info(f"\n{'='*70}")
            logger.info(f"Combining results from {len(results)} files (concatenating; no aggregation)...")
            logger.info(f"{'='*70}")
            combined = pd.concat(results.values(), ignore_index=True)
            combined_csv = output_dir / f"all-{group_by}-combined.csv"
            combined.to_csv(combined_csv, index=False)
            logger.info(f"\n{'='*70}")
            logger.info(f"✅ Combined results from {len(results)} files:")
            logger.info(f"   Total rows: {len(combined)} (one per file/feature; enrichment preserved)")
            logger.info(f"   Saved to: {combined_csv}")
            logger.info(f"{'='*70}")
        
        return results
