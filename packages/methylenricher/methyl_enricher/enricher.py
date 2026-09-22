"""
Core enrichment analysis functionality for MethylEnricher.

Supports MethylMapper combined CSV (all-gene_name-combined.csv) with optional
filtering by disease columns and DMP/gene metrics to focus on important genes.
"""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import pandas as pd


from .preset_registry import get_default_libraries, get_library_presets

# Evidence level order (higher index = stricter when used as min)
EVIDENCE_LEVEL_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}
# Legacy mapper/enricher config column names -> canonical mapper export columns.
SORT_BY_ALIASES = {"total_weight": "gene_importance"}

# Default libraries and named presets are authored in the committed registry
# (data/library_presets.json) and synced into cfg (kind enrichment_library_preset),
# not hardcoded here. Explicit `libraries` always takes precedence over presets.
DEFAULT_LIBRARIES = get_default_libraries()
LIBRARY_PRESETS = get_library_presets()

# Friendly aliases for CLI/config readability. Keys are normalized to lowercase.
_LIBRARY_ALIASES = {
    "chea 2022": "ChEA_2022",
    "encode tf chip-seq": "ENCODE_and_ChEA_Consensus_TFs_from_ChIP-X",
    "trrust": "TRRUST_Transcription_Factors_2019",
    "disgenet": "DisGeNET",
    "jensen diseases": "Jensen_DISEASES",
    "gwas catalog": "GWAS_Catalog_2019",
    "dsigdb": "DSigDB",
    "drugbank": "DGIdb_Drug_Targets_2024",
    "lincs l1000": "LINCS_L1000_Chem_Pert_up",
    "mirtarbase": "miRTarBase_2017",
}


def _normalize_library_name(name: str) -> str:
    """Normalize a human-friendly library name to an Enrichr key when possible."""
    cleaned = str(name).strip()
    if not cleaned:
        return cleaned
    return _LIBRARY_ALIASES.get(cleaned.lower(), cleaned)


def resolve_enrichr_libraries(
    libraries: Optional[List[str]] = None,
    library_preset: Optional[str] = None
) -> List[str]:
    """
    Resolve final Enrichr libraries from explicit list and/or named preset.

    Precedence:
      1) explicit libraries (if provided),
      2) library preset,
      3) DEFAULT_LIBRARIES.
    """
    if libraries:
        source = libraries
    elif library_preset:
        if library_preset not in LIBRARY_PRESETS:
            valid = ", ".join(sorted(LIBRARY_PRESETS))
            raise ValueError(f"Unknown library_preset '{library_preset}'. Valid presets: {valid}")
        source = LIBRARY_PRESETS[library_preset]
    else:
        source = DEFAULT_LIBRARIES

    # Preserve order and remove duplicates after normalization.
    resolved: List[str] = []
    seen = set()
    for raw in source:
        normalized = _normalize_library_name(raw)
        if normalized and normalized not in seen:
            resolved.append(normalized)
            seen.add(normalized)
    return resolved


class EnrichmentAnalyzer:
    """
    Perform Over-Representation Analysis (ORA) on gene lists using Enrichr databases.
    
    This class provides a clean API for running enrichment analysis and handles
    all the details of querying Enrichr, processing results, and saving outputs.
    """
    
    def __init__(
        self,
        libraries: Optional[List[str]] = None,
        library_preset: Optional[str] = None,
        organism: str = "Human",
        cutoff: float = 0.05,
        cisbp: Optional[Any] = None,
        cisbp_context: Optional[Any] = None,
    ):
        """
        Initialize the EnrichmentAnalyzer.
        
        Args:
            libraries: List of Enrichr library names (highest precedence)
            library_preset: Named preset when libraries are not provided
            organism: Organism name for Enrichr (default: "Human")
            cutoff: Adjusted p-value cutoff for filtering significant results
            cisbp: Optional CisbpConfig enabling the CIS-BP TF-motif integration
            cisbp_context: Optional CisbpContext (cache dir, GTF, genome FASTA, ...)
        """
        self.libraries = resolve_enrichr_libraries(
            libraries=libraries,
            library_preset=library_preset
        )
        self.organism = organism
        self.cutoff = cutoff
        self.cisbp = cisbp
        self.cisbp_context = cisbp_context
        self.results = {}

    @staticmethod
    def _resolve_sort_by(df: pd.DataFrame, sort_by: Optional[str]) -> Optional[str]:
        """Map legacy sort column names to canonical mapper columns when needed."""
        if not sort_by:
            return sort_by
        if sort_by in df.columns:
            return sort_by
        alias = SORT_BY_ALIASES.get(sort_by)
        if alias and alias in df.columns:
            print(f"[INFO] sort_by '{sort_by}' mapped to '{alias}' (canonical mapper column)")
            return alias
        return sort_by
        
    def _apply_csv_filters(
        self,
        df: pd.DataFrame,
        *,
        disease_only: bool = False,
        disease_column: str = "disease_associated",
        disease_association_types: Optional[List[str]] = None,
        min_disease_evidence_level: Optional[str] = None,
        min_disease_publications: Optional[int] = None,
        min_disease_score: Optional[float] = None,
        min_dmp_count: Optional[int] = None,
        min_unique_dmps: Optional[int] = None,
        max_gene_q_value: Optional[float] = None,
        min_gene_z: Optional[float] = None,
        min_gene_importance: Optional[float] = None,
        feature_types: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """Apply MethylMapper-style filters to a DataFrame with strict column checks."""
        out = df.copy()
        n_before = len(out)
        hits_cols = [c for c in ("hits_promoter", "hits_exon", "hits_intron", "hits_gene_body", "hits_terminator") if c in out.columns]
        if hits_cols:
            for c in hits_cols:
                out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0)
            hits_any = out[hits_cols].sum(axis=1) > 0
            out = out[hits_any]
            print(f"[INFO] Filter hits_* > 0: {len(out)} genes (was {n_before})")
            n_before = len(out)

        if disease_only and disease_column in out.columns:
            out = out[out[disease_column].astype(str).str.upper().isin(("TRUE", "1", "YES"))]
            print(f"[INFO] Filter disease_associated=True: {len(out)} genes (was {n_before})")
            n_before = len(out)

        if disease_association_types and "disease_association_type" in out.columns:
            allowed = {s.strip().lower() for s in disease_association_types}
            out = out[out["disease_association_type"].astype(str).str.strip().str.lower().isin(allowed)]
            print(f"[INFO] Filter disease_association_type in {allowed}: {len(out)} genes (was {n_before})")
            n_before = len(out)

        if min_disease_evidence_level is not None and "disease_evidence_level" in out.columns:
            min_level = EVIDENCE_LEVEL_ORDER.get(min_disease_evidence_level.lower(), 0)
            def _level_ok(val):
                if pd.isna(val): return False
                return EVIDENCE_LEVEL_ORDER.get(str(val).lower(), 0) >= min_level
            out = out[out["disease_evidence_level"].apply(_level_ok)]
            print(f"[INFO] Filter disease_evidence_level >= {min_disease_evidence_level}: {len(out)} genes (was {n_before})")
            n_before = len(out)

        if min_disease_publications is not None and "disease_publications" in out.columns:
            out = out[pd.to_numeric(out["disease_publications"], errors="coerce").fillna(0) >= min_disease_publications]
            print(f"[INFO] Filter disease_publications >= {min_disease_publications}: {len(out)} genes (was {n_before})")
            n_before = len(out)

        if min_disease_score is not None and "disease_score" in out.columns:
            out = out[pd.to_numeric(out["disease_score"], errors="coerce").fillna(0) >= min_disease_score]
            print(f"[INFO] Filter disease_score >= {min_disease_score}: {len(out)} genes (was {n_before})")
            n_before = len(out)

        if min_dmp_count is not None:
            # Canonical mapper output now exposes unique_dmps; dmp_count is legacy.
            if "dmp_count" in out.columns:
                count_series = pd.to_numeric(out["dmp_count"], errors="coerce").fillna(0)
                count_label = "dmp_count"
            elif "unique_dmps" in out.columns:
                count_series = pd.to_numeric(out["unique_dmps"], errors="coerce").fillna(0)
                count_label = "unique_dmps"
                print(
                    "[WARN] min_dmp_count is deprecated against mapper canonical schema; "
                    "applying threshold to unique_dmps."
                )
            else:
                raise ValueError(
                    "Filter min_dmp_count requested, but neither mapper column 'dmp_count' nor "
                    "'unique_dmps' is available."
                )
            out = out[count_series >= min_dmp_count]
            print(f"[INFO] Filter {count_label} >= {min_dmp_count}: {len(out)} genes (was {n_before})")
            n_before = len(out)

        if min_unique_dmps is not None:
            if (
                min_dmp_count is not None
                and min_unique_dmps == min_dmp_count
                and "dmp_count" not in out.columns
            ):
                print(
                    "[INFO] Skip min_unique_dmps filter: same threshold already applied via min_dmp_count"
                )
            elif "unique_dmps" not in out.columns:
                raise ValueError(
                    "Filter min_unique_dmps requested, but mapper column 'unique_dmps' is missing."
                )
            else:
                out = out[pd.to_numeric(out["unique_dmps"], errors="coerce").fillna(0) >= min_unique_dmps]
                print(f"[INFO] Filter unique_dmps >= {min_unique_dmps}: {len(out)} genes (was {n_before})")
                n_before = len(out)

        if max_gene_q_value is not None and "gene_q_value" in out.columns:
            gene_q = pd.to_numeric(out["gene_q_value"], errors="coerce")
            finite_mask = gene_q.notna()
            if finite_mask.any():
                keep_mask = (~finite_mask) | (gene_q <= max_gene_q_value)
                out = out[keep_mask]
                print(f"[INFO] Filter gene_q_value <= {max_gene_q_value}: {len(out)} genes (was {n_before})")
                n_before = len(out)
            else:
                print("[INFO] Skip gene_q_value filter: no finite gene_q_value values available.")

        if min_gene_z is not None and "gene_z" in out.columns:
            z = pd.to_numeric(out["gene_z"], errors="coerce")
            out = out[z.abs() >= min_gene_z]
            print(f"[INFO] Filter |gene_z| >= {min_gene_z}: {len(out)} genes (was {n_before})")
            n_before = len(out)

        if min_gene_importance is not None:
            if "gene_importance" not in out.columns:
                raise ValueError(
                    "Filter min_gene_importance requested, but mapper column 'gene_importance' is missing."
                )
            out = out[pd.to_numeric(out["gene_importance"], errors="coerce").fillna(0) >= min_gene_importance]
            print(f"[INFO] Filter gene_importance >= {min_gene_importance}: {len(out)} genes (was {n_before})")
            n_before = len(out)

        if feature_types:
            print("[WARN] Ignoring feature_types filter: feature_type-based filtering is removed; hits_* columns are used instead.")

        return out

    @staticmethod
    def _require_mapper_columns(df: pd.DataFrame, required: List[str], *, context: str) -> None:
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(
                f"{context}: missing required mapper columns {missing}. "
                f"Columns found: {list(df.columns)}"
            )

    def load_gene_list(
        self,
        input_path: Union[str, Path],
        top_n: Optional[int] = None,
        gene_column: Optional[str] = None,
        disease_only: bool = False,
        disease_column: str = "disease_associated",
        disease_association_types: Optional[List[str]] = None,
        min_disease_evidence_level: Optional[str] = None,
        min_disease_publications: Optional[int] = None,
        min_disease_score: Optional[float] = None,
        min_dmp_count: Optional[int] = None,
        min_unique_dmps: Optional[int] = None,
        max_gene_q_value: Optional[float] = None,
        min_gene_z: Optional[float] = None,
        min_gene_importance: Optional[float] = None,
        feature_types: Optional[List[str]] = None,
        sort_by: Optional[str] = None,
        sort_ascending: bool = False
    ) -> List[str]:
        """
        Load gene list from a text file or MethylMapper combined CSV.
        
        For CSV/TSV, strict mapper contract is enforced. Optional filters
        (from MethylMapper output) can be applied:
        disease_associated, disease_association_type, disease_evidence_level,
        disease_publications, disease_score, dmp_count, unique_dmps, gene_q_value,
        gene_z, gene_importance, feature_type.
        """
        input_path = Path(input_path)
        
        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        suffix = input_path.suffix.lower()
        if suffix in [".csv", ".tsv"]:
            sep = "," if suffix == ".csv" else "\t"
            df = pd.read_csv(input_path, sep=sep)
            if gene_column is None:
                gene_column = "gene_name"
            self._require_mapper_columns(
                df,
                [gene_column, "gene_importance", "unique_dmps"],
                context=f"Input mapper CSV {input_path}",
            )

            df = self._apply_csv_filters(
                df,
                disease_only=disease_only,
                disease_column=disease_column,
                disease_association_types=disease_association_types,
                min_disease_evidence_level=min_disease_evidence_level,
                min_disease_publications=min_disease_publications,
                min_disease_score=min_disease_score,
                min_dmp_count=min_dmp_count,
                min_unique_dmps=min_unique_dmps,
                max_gene_q_value=max_gene_q_value,
                min_gene_z=min_gene_z,
                min_gene_importance=min_gene_importance,
                feature_types=feature_types,
            )

            if sort_by is None and "gene_importance" in df.columns:
                sort_by = "gene_importance"
                print("[INFO] Sorting genes by gene_importance (auto)")

            sort_by = self._resolve_sort_by(df, sort_by)
            if sort_by:
                if sort_by in df.columns:
                    print(
                        f"[INFO] Sorting genes by {sort_by} "
                        f"({'asc' if sort_ascending else 'desc'})"
                    )
                    df = df.sort_values(by=sort_by, ascending=sort_ascending)
                else:
                    print(f"[WARN] sort_by '{sort_by}' not found; skipping sort")

            if len(df) == 0:
                raise ValueError(
                    "No genes left after filters. Try relaxing or removing filters "
                    "(e.g. omit --disease-only, or loosen disease_association_type / min_disease_score / etc.). "
                    "Or use --input with a plain gene list and no CSV filters."
                )

            genes_raw = df[gene_column].dropna().astype(str).tolist()
            genes = []
            seen = set()
            for gene in genes_raw:
                gene = gene.strip()
                if gene and gene not in seen:
                    genes.append(gene)
                    seen.add(gene)
            if top_n and len(genes) > top_n:
                genes = genes[:top_n]
                print(f"[INFO] Applied top_n={top_n}: {len(genes)} genes retained")
        else:
            if sort_by:
                print("[WARN] sort_by is only supported for CSV/TSV inputs; ignoring")
            with open(input_path) as f:
                genes = [line.strip() for line in f if line.strip()]

            if top_n and len(genes) > top_n:
                genes = genes[:top_n]

        print(f"[INFO] Loaded {len(genes)} gene symbols from {input_path}")
        return genes

    def _gene_weight_from_row(self, row: pd.Series) -> float:
        """Compute canonical per-gene weight from mapper gene_importance."""
        if "gene_importance" not in row.index or pd.isna(row.get("gene_importance")):
            raise ValueError("Mapper row is missing required 'gene_importance' for enrichment weighting.")
        try:
            return float(row["gene_importance"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid gene_importance value for enrichment weighting: {row.get('gene_importance')!r}") from exc

    def load_gene_list_with_weights(
        self,
        input_path: Union[str, Path],
        top_n: Optional[int] = None,
        gene_column: Optional[str] = None,
        disease_only: bool = False,
        disease_column: str = "disease_associated",
        disease_association_types: Optional[List[str]] = None,
        min_disease_evidence_level: Optional[str] = None,
        min_disease_publications: Optional[int] = None,
        min_disease_score: Optional[float] = None,
        min_dmp_count: Optional[int] = None,
        min_unique_dmps: Optional[int] = None,
        max_gene_q_value: Optional[float] = None,
        min_gene_z: Optional[float] = None,
        min_gene_importance: Optional[float] = None,
        feature_types: Optional[List[str]] = None,
        sort_by: Optional[str] = None,
        sort_ascending: bool = False,
    ) -> Tuple[List[str], Dict[str, float]]:
        """
        Load gene list and per-gene weights for weighted enrichment and module scoring.
        Returns (genes, weight_by_gene). Weights are prioritized from canonical
        mapper biological-importance columns.
        """
        input_path = Path(input_path)
        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")
        suffix = input_path.suffix.lower()
        if suffix not in [".csv", ".tsv"]:
            genes = self.load_gene_list(
                input_path, top_n=top_n, gene_column=gene_column,
                disease_only=disease_only, disease_column=disease_column,
                disease_association_types=disease_association_types,
                min_disease_evidence_level=min_disease_evidence_level,
                min_disease_publications=min_disease_publications,
                min_disease_score=min_disease_score,
                min_dmp_count=min_dmp_count,
                min_unique_dmps=min_unique_dmps,
                max_gene_q_value=max_gene_q_value,
                min_gene_z=min_gene_z,
                min_gene_importance=min_gene_importance,
                feature_types=feature_types,
                sort_by=sort_by,
                sort_ascending=sort_ascending,
            )
            return genes, {g: 1.0 for g in genes}
        sep = "," if suffix == ".csv" else "\t"
        df = pd.read_csv(input_path, sep=sep)
        gc = gene_column
        if gc is None:
            gc = "gene_name"
        self._require_mapper_columns(
            df,
            [gc, "gene_importance", "unique_dmps"],
            context=f"Input mapper CSV {input_path}",
        )
        df = self._apply_csv_filters(
            df,
            disease_only=disease_only,
            disease_column=disease_column,
            disease_association_types=disease_association_types,
            min_disease_evidence_level=min_disease_evidence_level,
            min_disease_publications=min_disease_publications,
            min_disease_score=min_disease_score,
            min_dmp_count=min_dmp_count,
            min_unique_dmps=min_unique_dmps,
            max_gene_q_value=max_gene_q_value,
            min_gene_z=min_gene_z,
            min_gene_importance=min_gene_importance,
            feature_types=feature_types,
        )
        if len(df) == 0:
            raise ValueError(
                "No genes left after filters. Try relaxing filters (e.g. --min-disease-score, "
                "--min-disease-evidence-level, --disease-only) or use an input generated with "
                "disease enrichment (Grok/Open Targets) if you need disease-associated genes."
            )
        if sort_by is None and "gene_importance" in df.columns:
            sort_by = "gene_importance"
        sort_by = self._resolve_sort_by(df, sort_by)
        if sort_by and sort_by in df.columns:
            df = df.sort_values(by=sort_by, ascending=sort_ascending)
        # One row per gene: take first occurrence (already sorted) for weight
        weight_by_gene: Dict[str, float] = {}
        genes_ordered: List[str] = []
        seen = set()
        for _, row in df.iterrows():
            g = str(row[gc]).strip() if pd.notna(row[gc]) else ""
            if not g or g in seen:
                continue
            seen.add(g)
            genes_ordered.append(g)
            weight_by_gene[g] = self._gene_weight_from_row(row)
        if top_n and len(genes_ordered) > top_n:
            genes_ordered = genes_ordered[:top_n]
            weight_by_gene = {g: weight_by_gene[g] for g in genes_ordered}
        print(f"[INFO] Loaded {len(genes_ordered)} gene symbols with weights from {input_path}")
        return genes_ordered, weight_by_gene

    def run_enrichment(
        self,
        genes: List[str],
        output_dir: Union[str, Path],
        *,
        gene_weights: Optional[Dict[str, float]] = None,
        force: bool = False,
        retry_policy: Optional["RetryPolicy"] = None,
    ) -> pd.DataFrame:
        """
        Run enrichment analysis for all libraries.
        
        Args:
            genes: List of gene symbols
            output_dir: Directory to save results
            
        Returns:
            Merged DataFrame with all enrichment results
        """
        from .enricher_completeness import (
            RetryPolicy,
            enrich_one_library,
            merge_library_results,
            primary_enrichment_rows,
        )
        import time

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        policy = retry_policy or RetryPolicy(max_retries=0)

        print(f"[INFO] Running enrichment analysis on {len(genes)} genes")
        print(f"[INFO] Libraries: {', '.join(self.libraries)}")

        for i, lib in enumerate(self.libraries):
            print(f"\n[INFO] Querying {lib}...")
            res = enrich_one_library(
                lib,
                genes,
                output_dir,
                organism=self.organism,
                policy=policy,
                force=force,
            )
            if res.success:
                print(f"[INFO] ✓ {lib}: {res.n_terms} terms found")
            else:
                print(f"[ERROR] Failed to process {lib}: {res.error_message}")
            if (
                i + 1 < len(self.libraries)
                and policy.inter_library_delay_seconds > 0
                and policy.max_retries > 0
            ):
                time.sleep(policy.inter_library_delay_seconds)

        self._maybe_run_cisbp(genes, output_dir)

        merged = merge_library_results(output_dir, self.libraries, cutoff=self.cutoff)
        if merged.empty:
            print("\n[WARN] No enrichment results found across any library")
            self.results = {"merged": merged, "significant": pd.DataFrame()}
            return merged
        merged = self._attach_weighted_overlap_metrics(merged, gene_weights)
        merged.to_csv(output_dir / "enrichment_merged.csv", index=False)

        print(f"\n[INFO] ✓ Merged results saved: {output_dir / 'enrichment_merged.csv'}")
        ranking = primary_enrichment_rows(merged)
        top_hits = (
            ranking[ranking["Adjusted P-value"] <= self.cutoff].copy().head(200)
            if not ranking.empty and "Adjusted P-value" in ranking.columns
            else pd.DataFrame()
        )
        self.results = {"merged": merged, "significant": top_hits}
        self._print_summary(merged, top_hits)
        return merged

    def _maybe_run_cisbp(self, genes: List[str], output_dir: Path) -> None:
        """
        Run the optional CIS-BP TF-motif integration and register its label so it
        merges with the Enrichr libraries. Soft-fails (warns) so a CIS-BP problem
        never breaks the core enrichment results.
        """
        if not self.cisbp or not getattr(self.cisbp, "enabled", False):
            return
        try:
            from .cisbp import run_cisbp, CisbpContext

            context = self.cisbp_context
            if context is None:
                context = CisbpContext(cutoff=self.cutoff)
            result = run_cisbp(self.cisbp, genes, output_dir, context=context)
            labels = (
                [str(x) for x in result if x]
                if isinstance(result, list)
                else ([str(result)] if result else [])
            )
            added = []
            for label in labels:
                if label not in self.libraries:
                    self.libraries.append(label)
                    added.append(label)
            if added:
                print(f"[INFO] ✓ CIS-BP: results added as {', '.join(added)}")
            elif not labels:
                print("[WARN] CIS-BP produced no enrichment terms; skipping")
        except NotImplementedError as exc:
            print(f"[WARN] CIS-BP skipped: {exc}")
        except Exception as exc:  # noqa: BLE001 - never break core enrichment
            print(f"[WARN] CIS-BP integration failed ({type(exc).__name__}): {exc}")

    def _attach_weighted_overlap_metrics(
        self,
        merged: pd.DataFrame,
        gene_weights: Optional[Dict[str, float]],
    ) -> pd.DataFrame:
        """
        Add overlap-weight metrics using per-gene effect-size/importance weights.

        Enrichr ORA itself remains unweighted, but these columns provide
        effect-size-aware pathway precision for downstream module scoring.
        """
        if merged.empty:
            return merged
        out = merged.copy()
        lookup = {
            str(k).strip().upper(): float(v)
            for k, v in (gene_weights or {}).items()
            if str(k).strip()
        }
        if not lookup:
            out["overlap_weight_mean"] = 1.0
            out["overlap_weight_abs_mean"] = 1.0
            out["overlap_weight_sum"] = 1.0
            out["overlap_weight_n"] = 0
            return out

        genes_col = "Genes" if "Genes" in out.columns else None
        overlap_col = "Overlap" if "Overlap" in out.columns else None
        means: List[float] = []
        abs_means: List[float] = []
        sums: List[float] = []
        counts: List[int] = []

        def _tokens_from_row(row: pd.Series) -> List[str]:
            if genes_col and pd.notna(row.get(genes_col)):
                raw = str(row.get(genes_col))
                sep = ";" if ";" in raw else ","
                toks = [t.strip().upper() for t in raw.split(sep) if t.strip()]
                if toks:
                    return toks
            if overlap_col and pd.notna(row.get(overlap_col)):
                raw = str(row.get(overlap_col))
                if "/" in raw:
                    left = raw.split("/", 1)[0]
                    sep = ";" if ";" in left else ","
                    toks = [t.strip().upper() for t in left.split(sep) if t.strip()]
                    if toks:
                        return toks
            return []

        for _, row in out.iterrows():
            toks = _tokens_from_row(row)
            vals = [lookup[g] for g in toks if g in lookup]
            if vals:
                means.append(float(sum(vals) / len(vals)))
                abs_means.append(float(sum(abs(v) for v in vals) / len(vals)))
                sums.append(float(sum(vals)))
                counts.append(int(len(vals)))
            else:
                means.append(0.0)
                abs_means.append(0.0)
                sums.append(0.0)
                counts.append(0)

        out["overlap_weight_mean"] = means
        out["overlap_weight_abs_mean"] = abs_means
        out["overlap_weight_sum"] = sums
        out["overlap_weight_n"] = counts
        return out
    
    def _print_summary(self, merged: pd.DataFrame, top_hits: pd.DataFrame):
        """Print a summary of enrichment results."""
        print("\n" + "=" * 70)
        print("ENRICHMENT ANALYSIS SUMMARY")
        print("=" * 70)
        print(f"Total terms tested: {len(merged):,}")
        print(f"Significant terms (q ≤ {self.cutoff}): {len(top_hits):,}")
        
        if not top_hits.empty:
            print(f"\nLibraries with significant hits:")
            lib_counts = top_hits.groupby("library").size().sort_values(ascending=False)
            for lib, count in lib_counts.items():
                print(f"  • {lib}: {count} terms")
            
            print(f"\nTop 5 most significant terms:")
            for i, row in top_hits.head(5).iterrows():
                print(f"  {i+1}. {row['Term']}")
                print(f"     Library: {row['library']}, q-value: {row['Adjusted P-value']:.2e}")
                print(f"     Overlap: {row['Overlap']}, Odds Ratio: {row['Odds Ratio']:.2f}")
        
        print("=" * 70)


def run_enrichment(
    input_file: Union[str, Path],
    output_dir: Union[str, Path],
    libraries: Optional[List[str]] = None,
    library_preset: Optional[str] = None,
    top_n: Optional[int] = 200,
    cutoff: float = 0.05,
    organism: str = "Human",
    gene_column: Optional[str] = None,
    disease_only: bool = False,
    disease_association_types: Optional[List[str]] = None,
    min_disease_evidence_level: Optional[str] = None,
    min_disease_publications: Optional[int] = None,
    min_disease_score: Optional[float] = None,
    min_dmp_count: Optional[int] = None,
    min_unique_dmps: Optional[int] = None,
    max_gene_q_value: Optional[float] = None,
    min_gene_z: Optional[float] = None,
    min_gene_importance: Optional[float] = None,
    feature_types: Optional[List[str]] = None,
    sort_by: Optional[str] = None,
    sort_ascending: bool = False,
    cisbp: Optional[Any] = None,
    cisbp_context: Optional[Any] = None,
) -> pd.DataFrame:
    """
    Convenience function to run enrichment analysis in one call.
    Accepts MethylMapper combined CSV and optional filters to focus on important genes.
    """
    analyzer = EnrichmentAnalyzer(
        libraries=libraries,
        library_preset=library_preset,
        organism=organism,
        cutoff=cutoff,
        cisbp=cisbp,
        cisbp_context=cisbp_context,
    )
    
    genes, gene_weights = analyzer.load_gene_list_with_weights(
        input_file,
        top_n=top_n,
        gene_column=gene_column,
        disease_only=disease_only,
        disease_association_types=disease_association_types,
        min_disease_evidence_level=min_disease_evidence_level,
        min_disease_publications=min_disease_publications,
        min_disease_score=min_disease_score,
        min_dmp_count=min_dmp_count,
        min_unique_dmps=min_unique_dmps,
        max_gene_q_value=max_gene_q_value,
        min_gene_z=min_gene_z,
        min_gene_importance=min_gene_importance,
        feature_types=feature_types,
        sort_by=sort_by,
        sort_ascending=sort_ascending
    )
    results = analyzer.run_enrichment(genes, output_dir, gene_weights=gene_weights)
    
    return results

