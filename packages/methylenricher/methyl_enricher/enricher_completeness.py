"""
Resumable Enrichr enrichment with retry/backoff and completeness assessment.
"""

from __future__ import annotations

import json
import random
import re
import time
import warnings
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .enricher import resolve_enrichr_libraries

# Smallest positive float; avoids log(0) in Combined Score and sort-order ties at 0.
_PVALUE_FLOOR = float(np.nextafter(0.0, 1.0))

TASK_STATUS_FILENAME = "enricher_task_status.json"
COMPLETENESS_MANIFEST_FILENAME = "enricher_completeness.json"


class EnrichErrorKind(str, Enum):
    RATE_LIMITED = "rate_limited"
    PARSE_ERROR = "parse_error"
    NETWORK = "network"
    VALIDATION = "validation"
    OTHER = "other"


@dataclass
class RetryPolicy:
    max_retries: int = 5
    base_seconds: float = 30.0
    max_seconds: float = 600.0
    inter_library_delay_seconds: float = 2.0

    @classmethod
    def from_config(cls, cfg: Optional[Dict[str, Any]] = None) -> "RetryPolicy":
        cfg = cfg or {}
        return cls(
            max_retries=int(cfg.get("enricher_max_retries", 5)),
            base_seconds=float(cfg.get("enricher_retry_base_seconds", 30.0)),
            max_seconds=float(cfg.get("enricher_retry_max_seconds", 600.0)),
            inter_library_delay_seconds=float(
                cfg.get("enricher_inter_library_delay_seconds", 2.0)
            ),
        )


@dataclass
class LibraryEnrichResult:
    library: str
    success: bool
    error_kind: Optional[str] = None
    error_message: Optional[str] = None
    attempts: int = 0
    output_path: Optional[str] = None
    n_terms: int = 0


@dataclass
class CompletenessReport:
    output_dir: str
    expected_libraries: List[str]
    present_libraries: List[str]
    missing_libraries: List[str]
    modules_required: bool
    modules_present: bool
    complete: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def library_csv_path(output_dir: Path, library: str) -> Path:
    return output_dir / f"enrich_{library}.csv"


def is_cisbp_library_label(name: object) -> bool:
    """True for CIS-BP merge labels (gene_sets, annotate, motif_scan, ...)."""
    token = str(name or "").strip().lower().replace("-", "_").replace(" ", "_")
    return token == "cis_bp" or token.startswith("cis_bp_")


def sanitize_enrichment_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clamp zero/negative p-values and recompute Combined Score safely.

    gseapy uses ``-log(p) * odds_ratio``; p=0 yields divide-by-zero warnings and
    non-finite scores that distort merged rankings.
    """
    if df.empty:
        return df
    out = df.copy()
    for col in ("P-value", "Adjusted P-value"):
        if col in out.columns:
            vals = pd.to_numeric(out[col], errors="coerce")
            out[col] = vals.clip(lower=_PVALUE_FLOOR).fillna(1.0)
    if (
        "Combined Score" in out.columns
        and "P-value" in out.columns
        and "Odds Ratio" in out.columns
    ):
        p = pd.to_numeric(out["P-value"], errors="coerce").clip(lower=_PVALUE_FLOOR)
        oddr = pd.to_numeric(out["Odds Ratio"], errors="coerce").fillna(0.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            score = -np.log(p.to_numpy(dtype=float)) * oddr.to_numpy(dtype=float)
        out["Combined Score"] = np.where(np.isfinite(score), score, np.nan)
    return out


def primary_enrichment_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Rows from Enrichr libraries only (exclude CIS-BP supporting annotations)."""
    if df.empty or "library" not in df.columns:
        return df
    mask = ~df["library"].map(is_cisbp_library_label)
    return df.loc[mask].copy()


def _sort_enrichment_frame(df: pd.DataFrame) -> pd.DataFrame:
    sort_cols = [c for c in ("Adjusted P-value", "P-value", "Odds Ratio") if c in df.columns]
    if not sort_cols:
        return df
    asc = [True, True, False][: len(sort_cols)]
    return df.sort_values(sort_cols, ascending=asc)


def is_valid_library_csv(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        df = pd.read_csv(path)
        return len(df) > 0
    except Exception:
        return False


def classify_enrich_error(exc: BaseException) -> EnrichErrorKind:
    msg = str(exc).lower()
    if "429" in msg or "too many requests" in msg or "rate limit" in msg:
        return EnrichErrorKind.RATE_LIMITED
    if "parsererror" in msg or "tokenizing data" in msg or "csv parsing" in msg:
        return EnrichErrorKind.PARSE_ERROR
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return EnrichErrorKind.NETWORK
    if "requests" in type(exc).__module__.lower():
        return EnrichErrorKind.NETWORK
    if "no genes" in msg or "empty" in msg and "gene" in msg:
        return EnrichErrorKind.VALIDATION
    return EnrichErrorKind.OTHER


def is_retryable(kind: EnrichErrorKind) -> bool:
    return kind in (
        EnrichErrorKind.RATE_LIMITED,
        EnrichErrorKind.PARSE_ERROR,
        EnrichErrorKind.NETWORK,
        EnrichErrorKind.OTHER,
    )


def backoff_seconds(attempt: int, policy: RetryPolicy) -> float:
    """Exponential backoff with jitter (attempt is 1-based)."""
    delay = min(policy.max_seconds, policy.base_seconds * (2 ** (attempt - 1)))
    jitter = random.uniform(0, delay * 0.25)
    return delay + jitter


def enrich_one_library(
    library: str,
    genes: List[str],
    output_dir: Path,
    *,
    organism: str = "Human",
    policy: Optional[RetryPolicy] = None,
    force: bool = False,
) -> LibraryEnrichResult:
    """
    Query Enrichr for one library with retries; write enrich_{library}.csv on success.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = library_csv_path(output_dir, library)
    policy = policy or RetryPolicy()

    if not force and is_valid_library_csv(out_path):
        df = pd.read_csv(out_path)
        return LibraryEnrichResult(
            library=library,
            success=True,
            attempts=0,
            output_path=str(out_path),
            n_terms=len(df),
        )

    if not genes:
        return LibraryEnrichResult(
            library=library,
            success=False,
            error_kind=EnrichErrorKind.VALIDATION.value,
            error_message="Empty gene list",
            attempts=0,
        )

    try:
        import gseapy as gp
    except ImportError as ex:
        return LibraryEnrichResult(
            library=library,
            success=False,
            error_kind=EnrichErrorKind.OTHER.value,
            error_message=str(ex),
            attempts=0,
        )

    max_attempts = max(1, policy.max_retries + 1)
    last_kind = EnrichErrorKind.OTHER
    last_msg = ""

    for attempt in range(1, max_attempts + 1):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                enr = gp.enrichr(
                    gene_list=genes,
                    gene_sets=[library],
                    outdir=str(output_dir),
                    cutoff=1.0,
                    background=None,
                    organism=(organism or "Human").strip().lower(),
                )
            if hasattr(enr, "results") and enr.results is not None and not enr.results.empty:
                df = sanitize_enrichment_df(enr.results.copy())
                df["library"] = library
                df.to_csv(out_path, index=False)
                return LibraryEnrichResult(
                    library=library,
                    success=True,
                    attempts=attempt,
                    output_path=str(out_path),
                    n_terms=len(df),
                )
            last_kind = EnrichErrorKind.OTHER
            last_msg = "No results returned"
        except Exception as exc:
            last_kind = classify_enrich_error(exc)
            last_msg = str(exc)

        if attempt < max_attempts and is_retryable(last_kind):
            wait = backoff_seconds(attempt, policy)
            print(
                f"[WARN] {library} attempt {attempt}/{max_attempts} failed "
                f"({last_kind.value}): {last_msg}; retry in {wait:.1f}s"
            )
            time.sleep(wait)
        elif attempt < max_attempts:
            break

    return LibraryEnrichResult(
        library=library,
        success=False,
        error_kind=last_kind.value,
        error_message=last_msg,
        attempts=max_attempts,
    )


def merge_library_results(
    output_dir: Path,
    libraries: Sequence[str],
    *,
    cutoff: float = 0.05,
) -> pd.DataFrame:
    """
    Build enrichment_merged.csv from per-library CSVs (no API calls).

    Enrichr libraries are ranked first; CIS-BP rows are appended as supporting
    TF-motif annotations so their scores cannot displace primary library hits.
    """
    output_dir = Path(output_dir)
    primary_frames: List[pd.DataFrame] = []
    supporting_frames: List[pd.DataFrame] = []
    for lib in libraries:
        path = library_csv_path(output_dir, lib)
        if not is_valid_library_csv(path):
            continue
        df = sanitize_enrichment_df(pd.read_csv(path))
        if "library" not in df.columns:
            df = df.copy()
            df["library"] = lib
        if is_cisbp_library_label(lib):
            supporting_frames.append(df)
        else:
            primary_frames.append(df)

    parts: List[pd.DataFrame] = []
    if primary_frames:
        parts.append(_sort_enrichment_frame(pd.concat(primary_frames, ignore_index=True)))
    if supporting_frames:
        parts.append(_sort_enrichment_frame(pd.concat(supporting_frames, ignore_index=True)))

    if not parts:
        merged = pd.DataFrame()
    else:
        merged = pd.concat(parts, ignore_index=True)

    merged_path = output_dir / "enrichment_merged.csv"
    merged.to_csv(merged_path, index=False)

    ranking = primary_enrichment_rows(merged)
    if not ranking.empty and "Adjusted P-value" in ranking.columns:
        top_hits = ranking[ranking["Adjusted P-value"] <= cutoff].head(200)
        if not top_hits.empty:
            top_hits.to_csv(output_dir / f"enrichment_top_q{cutoff}.csv", index=False)

    return merged


def assess_completeness(
    output_dir: Path,
    expected_libraries: Sequence[str],
    *,
    modules_required: bool = False,
) -> CompletenessReport:
    output_dir = Path(output_dir)
    present: List[str] = []
    missing: List[str] = []
    for lib in expected_libraries:
        if is_valid_library_csv(library_csv_path(output_dir, lib)):
            present.append(lib)
        else:
            missing.append(lib)

    modules_path = output_dir / "modules_ranked.csv"
    modules_present = modules_path.is_file() and modules_path.stat().st_size > 0
    complete = len(missing) == 0 and (not modules_required or modules_present)

    return CompletenessReport(
        output_dir=str(output_dir),
        expected_libraries=list(expected_libraries),
        present_libraries=present,
        missing_libraries=missing,
        modules_required=modules_required,
        modules_present=modules_present,
        complete=complete,
    )


def write_task_status(
    output_dir: Path,
    *,
    comparison_label: str,
    status: str,
    libraries_ok: List[str],
    libraries_missing: List[str],
    library_results: Optional[List[LibraryEnrichResult]] = None,
    modules_required: bool = False,
    modules_present: bool = False,
) -> Path:
    output_dir = Path(output_dir)
    payload: Dict[str, Any] = {
        "comparison_label": comparison_label,
        "status": status,
        "libraries_ok": libraries_ok,
        "libraries_missing": libraries_missing,
        "modules_required": modules_required,
        "modules_present": modules_present,
    }
    if library_results:
        payload["library_attempts"] = [
            {
                "library": r.library,
                "success": r.success,
                "attempts": r.attempts,
                "error_kind": r.error_kind,
                "error_message": r.error_message,
            }
            for r in library_results
        ]
    path = output_dir / TASK_STATUS_FILENAME
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def read_task_status(output_dir: Path) -> Optional[Dict[str, Any]]:
    path = Path(output_dir) / TASK_STATUS_FILENAME
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_expected_libraries(
    libraries: Optional[List[str]] = None,
    library_preset: Optional[str] = None,
) -> List[str]:
    return resolve_enrichr_libraries(libraries=libraries, library_preset=library_preset)


def production_enricher_root(project_json: Path) -> Path:
    """Parent enricher dir for production project (sibling of comparison subdirs)."""
    project_json = Path(project_json)
    from methyl_utils import load_project

    project = load_project(project_json)
    root = Path(project.get_project_root())
    # The manifest/queue live at the top-level ``enricher/`` dir, never a control-group
    # subdir (comparison outputs may nest as ``enricher/<control>/<comparison>``).
    comparisons = project.get_comparisons() if hasattr(project, "get_comparisons") else []
    if comparisons:
        c0 = comparisons[0]
        enr = Path(project.get_enricher_output_dir(c0.control_group, c0.disease_group))
        for ancestor in (enr, *enr.parents):
            if ancestor.name == "enricher":
                return ancestor
        return enr.parent
    return root / "enricher"


def completeness_manifest_path(project_json: Path) -> Path:
    return production_enricher_root(project_json) / COMPLETENESS_MANIFEST_FILENAME