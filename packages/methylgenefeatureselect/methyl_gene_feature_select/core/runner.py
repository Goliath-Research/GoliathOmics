"""Structural (gene × region) ECDF OvR k-search — Phase 3 scaffold."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

logger = logging.getLogger(__name__)

GENE_FEATURES_CLASSIFIER_CSV = "gene-features-classifier.csv"
GENE_FEATURE_SELECTION_JSON = "gene_feature_selection.json"

REGION_TYPES = ("promoter", "exon", "intron", "gene_body", "terminator")


def _gene_summary_paths(mapper_dir: Path) -> List[Path]:
    candidates = [
        mapper_dir / "all-gene_name-combined.csv",
        *sorted(mapper_dir.glob("*-features-gene_name.csv")),
        *sorted(mapper_dir.glob("chr*-gene_name.csv")),
    ]
    return [p for p in candidates if p.is_file()]


def _feature_importance_column(feature_type: str) -> str:
    token = str(feature_type or "gene_body").strip().lower()
    if token not in REGION_TYPES:
        token = "gene_body"
    return f"feature_importance_{token}"


def _rank_structural_catalog(mapper_dir: Path, catalog: pd.DataFrame) -> pd.DataFrame:
    """Attach mapper feature_importance_* scores and sort features by descending rank."""
    if catalog.empty:
        return catalog
    scores: Dict[tuple[str, str], float] = {}
    for path in _gene_summary_paths(mapper_dir):
        try:
            df = pd.read_csv(path)
        except Exception:
            continue
        if "gene_name" not in df.columns:
            continue
        for _, row in df.iterrows():
            gene = str(row.get("gene_name") or "").strip()
            if not gene:
                continue
            for feature in REGION_TYPES:
                col = f"feature_importance_{feature}"
                if col not in df.columns:
                    continue
                val = pd.to_numeric(row.get(col), errors="coerce")
                if pd.isna(val):
                    continue
                key = (gene, feature)
                scores[key] = max(float(val), scores.get(key, float("-inf")))
    out = catalog.copy()
    out["rank_score"] = [
        scores.get((str(g), str(t)), 0.0)
        for g, t in zip(out["gene_name"], out["feature_type"])
    ]
    out = out.sort_values(["rank_score", "gene_name"], ascending=[False, True]).reset_index(drop=True)
    return out


def _intersection_paths(mapper_dir: Path) -> List[Path]:
    return sorted(mapper_dir.glob("*-intersections.csv"))


def build_structural_feature_catalog(mapper_dir: Path) -> pd.DataFrame:
    """Build candidate (gene_name, feature_type) rows from mapper intersection CSVs."""
    rows: List[Dict[str, Any]] = []
    for path in _intersection_paths(mapper_dir):
        try:
            df = pd.read_csv(path)
        except Exception:
            continue
        if "gene_name" not in df.columns:
            continue
        feature_type = "gene_body"
        stem = path.stem.replace("-intersections", "")
        for token in REGION_TYPES:
            if token in stem.lower():
                feature_type = token
                break
        for gene in df["gene_name"].astype(str).tolist():
            gene = str(gene).strip()
            if gene and gene.lower() not in {"unknown", "nan", "none"}:
                rows.append({"gene_name": gene, "feature_type": feature_type})
    if not rows:
        return pd.DataFrame(columns=["gene_name", "feature_type"])
    out = pd.DataFrame(rows).drop_duplicates().reset_index(drop=True)
    return out


def gene_feature_outputs_exist(output_dir: Path) -> bool:
    return (output_dir / GENE_FEATURES_CLASSIFIER_CSV).is_file() and (
        output_dir / GENE_FEATURE_SELECTION_JSON
    ).is_file()


def run_gene_feature_selection(
    *,
    mapper_dir: Path,
    output_dir: Path,
    target_balanced_accuracy: Optional[float] = None,
    max_features: Optional[int] = None,
    region_types: Optional[Sequence[str]] = None,
    skip_if_exists: bool = True,
) -> Dict[str, Any]:
    """
    Scaffold: export ranked structural feature catalog; full ECDF OvR k-search TBD.

    When ``target_balanced_accuracy`` is set, future versions will run prefix k-search
    using methyl_utils ECDF OvR (same engine as methyl-gene-select).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if skip_if_exists and gene_feature_outputs_exist(output_dir):
        logger.info("Gene-feature selection outputs exist; skipping")
        with open(output_dir / GENE_FEATURE_SELECTION_JSON, encoding="utf-8") as f:
            return {"status": "skipped", "audit": json.load(f)}

    catalog = build_structural_feature_catalog(Path(mapper_dir))
    if region_types:
        allowed = {str(r).strip().lower() for r in region_types}
        catalog = catalog[catalog["feature_type"].astype(str).str.lower().isin(allowed)]
    if catalog.empty:
        raise ValueError(f"No structural features found under mapper dir {mapper_dir}")

    ranked = _rank_structural_catalog(mapper_dir, catalog)
    if max_features is not None and int(max_features) > 0:
        ranked = ranked.head(int(max_features)).copy()
    elif target_balanced_accuracy is not None:
        cap = min(len(ranked), max(50, int(len(ranked) * 0.25)))
        ranked = ranked.head(cap).copy()

    out_csv = output_dir / GENE_FEATURES_CLASSIFIER_CSV
    ranked.to_csv(out_csv, index=False)
    audit = {
        "n_features": int(len(ranked)),
        "n_catalog": int(len(catalog)),
        "target_balanced_accuracy": target_balanced_accuracy,
        "max_features": max_features,
        "region_types": list(region_types) if region_types else list(REGION_TYPES),
        "selection_mode": "ranked_catalog",
    }
    with open(output_dir / GENE_FEATURE_SELECTION_JSON, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2)
    return {"status": "ok", "audit": audit, "output_csv": str(out_csv)}
