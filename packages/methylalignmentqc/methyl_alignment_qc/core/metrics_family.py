"""Detect alignment QC metrics family (Parabricks vs Mojo linear vs methylGrapher WGBS)."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


class MetricsFamily(str, Enum):
    PARABRICKS = "parabricks"
    MOJO_LINEAR = "mojo_linear"
    METHYLGRAPHER_WGBS = "methylgrapher_wgbs"


_WGBS_MODES = frozenset({"pangenome_wgbs", "wgbs_pangenome", "methylgrapher_wgbs"})
_PARABRICKS_MODES = frozenset({"linear", "pangenome", "fq2bam", "giraffe"})


def normalize_alignment_mode(alignment_mode: Optional[str]) -> Optional[str]:
    if alignment_mode is None:
        return None
    mode = str(alignment_mode).strip().lower()
    return mode or None


def find_alignment_metrics_json(sample_dir: Path, sample_name: str) -> Optional[Path]:
    candidate = Path(sample_dir) / f"{sample_name}.alignment_metrics.json"
    return candidate if candidate.is_file() else None


def find_linear_metrics_json(sample_dir: Path, sample_name: str) -> Optional[Path]:
    candidate = Path(sample_dir) / f"{sample_name}.json"
    return candidate if candidate.is_file() else None


def load_alignment_metrics_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"alignment_metrics.json must be an object: {path}")
    return raw


def is_methylgrapher_provenance(raw: Dict[str, Any]) -> bool:
    tool = str(raw.get("tool") or "").lower()
    return "methylgrapher" in tool or str(raw.get("action") or "").startswith(
        "sample.methylgrapher"
    )


def is_mojo_linear_metrics_payload(raw: Dict[str, Any]) -> bool:
    """True when MojoFq2bamMeth emitted samtools+placeholder Picard-shaped JSON."""
    if not isinstance(raw, dict):
        return False
    engine = str(raw.get("engine") or "").lower()
    if engine in {"mojo_fq2bam_meth", "mojo-fq2bam-meth", "mojo"}:
        return True
    source = str(raw.get("metrics_source") or "").lower()
    if "placeholder" in source:
        return True
    placeholders = raw.get("placeholder_fields")
    return isinstance(placeholders, list) and len(placeholders) > 0


def peek_linear_metrics_json(
    sample_dir: Path, sample_name: str
) -> Optional[Dict[str, Any]]:
    path = find_linear_metrics_json(sample_dir, sample_name)
    if path is None:
        return None
    try:
        return load_alignment_metrics_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def detect_metrics_family(
    sample_dir: Path,
    sample_name: str,
    *,
    alignment_mode: Optional[str] = None,
    parabricks_available: bool = False,
) -> Tuple[MetricsFamily, Optional[Path], Optional[Dict[str, Any]]]:
    """
    Resolve metrics family for a sample directory.

    Returns ``(family, provenance_path_or_none, provenance_dict_or_none)``.

    When ``alignment_mode`` is ``pangenome_wgbs``, never treat a stale linear
    Picard tar / stub JSON as the metrics source — require methylGrapher provenance.

    When mode is omitted and both methylGrapher provenance and Parabricks/Mojo
    linear metrics are present, fail closed and require ``alignmentMode``.
    """
    sample_dir = Path(sample_dir)
    mode = normalize_alignment_mode(alignment_mode)
    prov_path = find_alignment_metrics_json(sample_dir, sample_name)
    prov: Optional[Dict[str, Any]] = None
    if prov_path is not None:
        try:
            prov = load_alignment_metrics_json(prov_path)
        except (OSError, ValueError, json.JSONDecodeError):
            prov = None

    linear_payload = peek_linear_metrics_json(sample_dir, sample_name)
    mojo_linear = bool(linear_payload and is_mojo_linear_metrics_payload(linear_payload))

    if mode in _WGBS_MODES:
        if prov is None or not is_methylgrapher_provenance(prov):
            raise RuntimeError(
                f"alignmentMode={mode} requires {sample_name}.alignment_metrics.json "
                f"with tool=methylGrapher under {sample_dir} "
                "(refusing stale linear/Parabricks qc-metrics fallback)"
            )
        return MetricsFamily.METHYLGRAPHER_WGBS, prov_path, prov

    if mode in _PARABRICKS_MODES:
        if mojo_linear:
            return MetricsFamily.MOJO_LINEAR, None, None
        if not parabricks_available and linear_payload is None:
            raise RuntimeError(
                f"alignmentMode={mode} requires Parabricks/Picard or MojoFq2bamMeth metrics "
                f"({sample_name}.json or Picard tables in {sample_name}.qc-metrics.tar)"
            )
        if not parabricks_available and not mojo_linear:
            # JSON present but not recognized as Mojo placeholders and no Picard tar
            if linear_payload is not None:
                return MetricsFamily.PARABRICKS, None, None
            raise RuntimeError(
                f"alignmentMode={mode} requires Parabricks/Picard metrics "
                f"({sample_name}.json or Picard tables in {sample_name}.qc-metrics.tar)"
            )
        return MetricsFamily.PARABRICKS, None, None

    # Infer from artifacts when mode omitted — fail closed if ambiguous.
    mg_ok = prov is not None and is_methylgrapher_provenance(prov)
    linear_ok = bool(parabricks_available or linear_payload is not None)
    if mg_ok and linear_ok:
        raise RuntimeError(
            f"Ambiguous alignment QC artifacts for {sample_name} in {sample_dir}: "
            f"both methylGrapher {sample_name}.alignment_metrics.json and "
            f"linear/Parabricks metrics are present. Pass alignmentMode "
            f"(linear|pangenome|pangenome_wgbs) on sample.methyl_qc."
        )
    if mg_ok:
        return MetricsFamily.METHYLGRAPHER_WGBS, prov_path, prov
    if mojo_linear:
        return MetricsFamily.MOJO_LINEAR, None, None
    if parabricks_available or linear_payload is not None:
        return MetricsFamily.PARABRICKS, None, None

    raise RuntimeError(
        f"Missing Parabricks metrics for {sample_name}: expected a full "
        f"{sample_dir / f'{sample_name}.json'} or "
        f"{sample_dir / f'{sample_name}.qc-metrics.tar'} "
        f"(or methylGrapher {sample_name}.alignment_metrics.json for pangenome_wgbs). "
        f"Pass alignmentMode on sample.methyl_qc when artifacts are ambiguous."
    )
