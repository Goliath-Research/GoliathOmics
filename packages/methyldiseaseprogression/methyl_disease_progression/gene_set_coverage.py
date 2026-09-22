"""
Disease-aware per-stage overlap metrics between mapper gene universes and curated gene sets.

Supports nested ``step_config.progression`` fields from the project plan (``disease_context``,
``gene_set_profile``, ``gene_set_metrics``) plus legacy flat keys for backward compatibility.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

from .progression import StageSpec, _first_existing_column

logger = logging.getLogger(__name__)

_PROFILE_DIR = Path(__file__).resolve().parent / "profiles"


def normalize_progression_gene_set_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge nested plan-style keys into the internal flat shape used for computation and I/O.

    New shape (``gene_set_metrics`` sub-object):
    - ``enabled`` -> ``gene_set_metrics_enabled``
    - ``gene_universe``: ``mapper_all`` | ``mapper_top_n`` | ``mapper_min_weight_quantile``
    - ``top_n``, ``weight_quantile`` (optional; map to ``gene_set_top_n``, ``gene_set_weight_quantile``)
    - ``output_basename`` (default ``stage_gene_set_fractions`` when nested block is present)

    Profile:
    - ``gene_set_profile`` as path string, ``{"path": "..."}``, or ``{"categories": [...]}``
    - ``disease_context`` (e.g. ``prostate_cancer``) resolves bundled ``profiles/<name>.json``

    Legacy flat keys (``gene_set_metrics_enabled``, ``gene_sets_path``, ``disease_profile``, etc.)
    still apply when the nested block does not override them.
    """
    out: Dict[str, Any] = dict(raw)
    gsm = raw.get("gene_set_metrics")

    nested_present = isinstance(gsm, dict)
    if nested_present:
        g = gsm
        if "enabled" in g:
            out["gene_set_metrics_enabled"] = bool(g["enabled"])
        gu = g.get("gene_universe")
        if gu is not None:
            gu = str(gu).strip()
            if gu == "mapper_all":
                out["gene_set_denominator"] = "all_genes"
            elif gu == "mapper_top_n":
                out["gene_set_denominator"] = "top_n"
                if "top_n" in g:
                    out["gene_set_top_n"] = g["top_n"]
            elif gu == "mapper_min_weight_quantile":
                out["gene_set_denominator"] = "min_weight_quantile"
                if "weight_quantile" in g:
                    out["gene_set_weight_quantile"] = g["weight_quantile"]
            else:
                logger.warning("Unknown gene_universe %r; keeping existing denominator settings", gu)

        ob = g.get("output_basename")
        if ob is not None:
            base = str(ob).strip()
            for suffix in (".csv", ".json"):
                if base.lower().endswith(suffix):
                    base = base[: -len(suffix)]
            out["_gene_set_output_basename"] = base

    gsp = raw.get("gene_set_profile")
    if gsp is not None:
        if isinstance(gsp, str):
            out["gene_sets_path"] = str(Path(gsp).expanduser())
            out.pop("_gene_set_profile_categories", None)
        elif isinstance(gsp, dict):
            if "path" in gsp:
                out["gene_sets_path"] = str(Path(gsp["path"]).expanduser())
                out.pop("_gene_set_profile_categories", None)
            elif "categories" in gsp:
                out["_gene_set_profile_categories"] = gsp["categories"]
                out.pop("gene_sets_path", None)
            else:
                # Plain category -> list JSON object (same as legacy file format).
                if gsp and all(isinstance(v, list) for v in gsp.values()):
                    out["_gene_set_profile_dict"] = gsp
                    out.pop("gene_sets_path", None)

    dc = raw.get("disease_context")
    if dc and not out.get("gene_sets_path") and not out.get("_gene_set_profile_categories") and not out.get(
        "_gene_set_profile_dict"
    ):
        key = str(dc).strip()
        if key:
            out["disease_profile"] = key

    # Default output basename: plan default for new-style config; legacy filename otherwise.
    if "_gene_set_output_basename" not in out:
        if nested_present:
            out["_gene_set_output_basename"] = "stage_gene_set_fractions"
        elif out.get("gene_set_metrics_enabled"):
            out["_gene_set_output_basename"] = "stage_gene_set_metrics"
        else:
            out["_gene_set_output_basename"] = "stage_gene_set_fractions"

    return out


def _dict_from_categories(categories: Any) -> Dict[str, List[str]]:
    if not isinstance(categories, list):
        return {}
    out: Dict[str, List[str]] = {}
    for item in categories:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        if not cid:
            continue
        genes = item.get("genes")
        if not isinstance(genes, list):
            continue
        out[str(cid).strip()] = [str(x).strip() for x in genes if str(x).strip()]
    return out


def _load_json_file(path: Path) -> Dict[str, List[str]]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"Gene set profile must be a JSON object: {path}")
    # New format: { "categories": [ { "id", "label", "genes" } ] }
    if "categories" in raw and isinstance(raw["categories"], list):
        return _dict_from_categories(raw["categories"])
    out: Dict[str, List[str]] = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        if isinstance(v, list):
            out[k.strip()] = [str(x).strip().upper() for x in v if str(x).strip()]
        else:
            logger.warning("Skipping profile key %r (expected list of symbols)", k)
    return out


def load_gene_set_profile(
    progression_cfg: Dict[str, Any],
) -> Tuple[Dict[str, Set[str]], Optional[str]]:
    """
    Resolve category -> gene symbols from normalized progression config.

    Returns (category -> set of upper-case symbols, source description).
    """
    cats = progression_cfg.get("_gene_set_profile_categories")
    if cats is not None:
        flat = _dict_from_categories(cats)
        return {k: set(v) for k, v in flat.items()}, "inline:gene_set_profile.categories"

    gd = progression_cfg.get("_gene_set_profile_dict")
    if isinstance(gd, dict):
        raw = _load_json_profile_from_mapping(gd)
        return {k: set(v) for k, v in raw.items()}, "inline:gene_set_profile"

    path_str = progression_cfg.get("gene_sets_path")
    if path_str:
        p = Path(str(path_str)).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"gene_sets_path not found: {p}")
        raw = _load_json_file(p)
        return {k: set(v) for k, v in raw.items()}, str(p)

    profile_key = progression_cfg.get("disease_profile")
    if profile_key:
        key = str(profile_key).strip()
        if not key or any(c for c in key if c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."):
            raise ValueError(f"Invalid disease_profile / disease_context key: {profile_key!r}")
        bundled = _PROFILE_DIR / f"{key}.json"
        if not bundled.is_file():
            raise FileNotFoundError(
                f"Bundled disease profile not found: {bundled} "
                f"(place {key}.json under profiles/ or set gene_sets_path / gene_set_profile)"
            )
        raw = _load_json_file(bundled)
        return {k: set(v) for k, v in raw.items()}, str(bundled)

    return {}, None


def _load_json_profile_from_mapping(raw: Dict[str, Any]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        if isinstance(v, list):
            out[k.strip()] = [str(x).strip().upper() for x in v if str(x).strip()]
        else:
            logger.warning("Skipping profile key %r (expected list of symbols)", k)
    return out


def _genes_for_stage_universe(
    stage: StageSpec,
    *,
    denominator: str,
    top_n: Optional[int],
    weight_quantile: Optional[float],
) -> Tuple[Set[str], int]:
    if not stage.mapper_combined_csv.exists():
        return set(), 0

    df = pd.read_csv(stage.mapper_combined_csv)
    required = ["gene_name", "gene_importance"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"{stage.comparison_label}: mapper combined CSV missing required columns {missing} "
            f"at {stage.mapper_combined_csv}"
        )
    gene_col = "gene_name"
    score_col = "gene_importance"
    work = df[[gene_col]].copy()
    work[gene_col] = work[gene_col].astype(str).str.strip()
    work = work[work[gene_col] != ""]
    work["_w"] = pd.to_numeric(df.loc[work.index, score_col], errors="coerce").fillna(0.0)

    agg = (
        work.groupby(gene_col, as_index=False)
        .agg(_w=("_w", "max"))
        .sort_values("_w", ascending=False)
        .reset_index(drop=True)
    )

    mode = (denominator or "all_genes").strip().lower()
    if mode == "all_genes":
        genes = set(agg[gene_col].astype(str).str.upper())
        return genes, len(agg)

    if mode == "top_n":
        n = int(top_n or 500)
        n = max(1, min(n, len(agg)))
        top = agg.head(n)
        genes = set(top[gene_col].astype(str).str.upper())
        return genes, n

    if mode in ("min_weight_quantile", "weight_quantile"):
        q = float(weight_quantile if weight_quantile is not None else 0.5)
        q = min(1.0, max(0.0, q))
        thresh = float(agg["_w"].quantile(q))
        filt = agg[agg["_w"] >= thresh]
        genes = set(filt[gene_col].astype(str).str.upper())
        return genes, len(filt)

    logger.warning("Unknown gene_set_denominator %r; using all_genes", denominator)
    genes = set(agg[gene_col].astype(str).str.upper())
    return genes, len(agg)


def compute_gene_set_fractions(
    stage_specs: List[StageSpec],
    progression_cfg: Dict[str, Any],
) -> pd.DataFrame:
    """
    One row per (stage, category) with n_universe, n_overlap, fraction.
    """
    cfg = normalize_progression_gene_set_config(progression_cfg)

    if not cfg.get("gene_set_metrics_enabled"):
        return pd.DataFrame()

    try:
        profile, src = load_gene_set_profile(cfg)
    except (FileNotFoundError, ValueError) as e:
        logger.warning("Gene set metrics skipped: %s", e)
        return pd.DataFrame()

    if not profile:
        logger.info("Gene set metrics: no profile resolved; skipped.")
        return pd.DataFrame()

    denom = str(cfg.get("gene_set_denominator") or "all_genes").strip()
    top_n = cfg.get("gene_set_top_n")
    wq = cfg.get("gene_set_weight_quantile")
    disease_ctx = cfg.get("disease_context") or cfg.get("disease_profile")

    rows: List[Dict[str, Any]] = []
    for spec in stage_specs:
        try:
            genes, _n_hint = _genes_for_stage_universe(
                spec,
                denominator=denom,
                top_n=int(top_n) if top_n is not None else None,
                weight_quantile=float(wq) if wq is not None else None,
            )
        except Exception as e:
            logger.warning(
                "Skipping gene set metrics for %s due to mapper schema/input error: %s",
                spec.comparison_label,
                e,
            )
            continue
        n_u = max(len(genes), 1)
        for cat_id, sym_set in sorted(profile.items()):
            overlap = genes & sym_set
            n_o = len(overlap)
            frac = float(n_o) / float(n_u)
            rows.append(
                {
                    "stage_index": spec.stage_index,
                    "comparison_label": spec.comparison_label,
                    "disease_group": spec.disease_group,
                    "control_group": spec.control_group,
                    "disease_context": disease_ctx,
                    "category_id": cat_id,
                    "gene_set_profile_source": src,
                    "gene_set_denominator": denom,
                    "n_universe": int(n_u),
                    "n_overlap": int(n_o),
                    "fraction": frac,
                }
            )

    return pd.DataFrame(rows)


def gene_set_fractions_summary(df: pd.DataFrame, raw_cfg: Dict[str, Any]) -> Dict[str, Any]:
    ncfg = normalize_progression_gene_set_config(raw_cfg)
    gsm = raw_cfg.get("gene_set_metrics")
    gene_universe = gsm.get("gene_universe") if isinstance(gsm, dict) else None
    if df.empty:
        return {
            "enabled": False,
            "rows": 0,
            "disease_context": raw_cfg.get("disease_context") or raw_cfg.get("disease_profile"),
            "output_basename": ncfg.get("_gene_set_output_basename"),
            "gene_universe": gene_universe,
        }
    return {
        "enabled": True,
        "rows": int(len(df)),
        "categories": sorted(df["category_id"].unique().tolist()) if "category_id" in df.columns else [],
        "profile_source": str(df["gene_set_profile_source"].iloc[0])
        if "gene_set_profile_source" in df.columns and len(df)
        else None,
        "disease_context": raw_cfg.get("disease_context") or raw_cfg.get("disease_profile"),
        "output_basename": ncfg.get("_gene_set_output_basename"),
        "gene_universe": gene_universe,
    }


def build_gene_set_fractions_json_payload(df: pd.DataFrame) -> Dict[str, Any]:
    """Summary structure for ``stage_gene_set_fractions.json`` (plot-friendly)."""
    if df.empty:
        return {"rows": []}
    records = df.sort_values(["stage_index", "category_id"]).to_dict(orient="records")
    clean: List[Dict[str, Any]] = []
    for r in records:
        row: Dict[str, Any] = {}
        for k, v in r.items():
            key = str(k)
            if k == "fraction":
                row[key] = float(v)
            elif hasattr(v, "item"):
                row[key] = v.item()
            else:
                row[key] = v
        clean.append(row)
    return {
        "rows": clean,
        "n_rows": len(clean),
    }
