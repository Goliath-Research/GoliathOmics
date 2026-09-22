"""
cfDNA / WGBS fragment-length metrics and guardrails from insert-size histograms.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..models.config import FragmentomicsConfig
from ..models.sample_qc import FragmentomicsGuardrailDetails, FragmentomicsMetrics, GuardrailMetric


def _histogram_pairs(payload: Dict[str, Any]) -> List[Tuple[int, int]]:
    """Extract (insert_size_bp, count) from V1 columnar or V2 row-oriented histogram."""
    hist = payload.get("insert_size_histogram") or {}
    if isinstance(hist, dict) and "rows" in hist:
        rows = hist.get("rows") or []
        out: List[Tuple[int, int]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            size = int(row.get("insert_size", 0))
            count = int(row.get("all_reads_fr_count", row.get("All_Reads.fr_count", 0)))
            if count > 0:
                out.append((size, count))
        return out
    sizes = hist.get("insert_size") or []
    counts = hist.get("All_Reads.fr_count") or hist.get("all_reads_fr_count") or []
    return [
        (int(sizes[i]), int(counts[i]))
        for i in range(min(len(sizes), len(counts)))
        if int(counts[i]) > 0
    ]


def _mode_in_window(pairs: List[Tuple[int, int]], lo: int, hi: int) -> Optional[int]:
    window = [(s, c) for s, c in pairs if lo <= s <= hi]
    if not window:
        return None
    return max(window, key=lambda x: x[1])[0]


def compute_fragmentomics_metrics(
    payload: Dict[str, Any],
    cfg: FragmentomicsConfig,
) -> FragmentomicsMetrics:
    """Derive fragment-length summary metrics from insert-size histogram."""
    pairs = _histogram_pairs(payload)
    total = sum(c for _, c in pairs)
    ins = payload.get("insert_size_metrics") or {}
    median = int(ins.get("median_insert_size", 0))

    short_frac = 0.0
    long_frac = 0.0
    if total > 0:
        short_max = int(cfg.short_fragment_max_bp)
        short_frac = sum(c for s, c in pairs if s <= short_max) / total
        long_min = int(cfg.long_fragment_min_bp or 300)
        long_frac = sum(c for s, c in pairs if s >= long_min) / total

    peak_lo = int(cfg.nucleosome_peak_bp_min)
    peak_hi = int(cfg.nucleosome_peak_bp_max)
    nucleosome_peak = _mode_in_window(pairs, peak_lo, peak_hi)
    if nucleosome_peak is None and pairs:
        nucleosome_peak = max(pairs, key=lambda x: x[1])[0]

    return FragmentomicsMetrics(
        profile=str(cfg.resolved_profile()),
        median_insert_size=median,
        short_fragment_fraction=round(short_frac, 6),
        long_fragment_fraction=round(long_frac, 6),
        nucleosome_peak_bp=nucleosome_peak,
        short_fragment_max_bp=int(cfg.short_fragment_max_bp),
        nucleosome_peak_bp_min=peak_lo,
        nucleosome_peak_bp_max=peak_hi,
    )


def _guardrail_metric(
    *,
    value: float,
    normal_range: str,
    passed: bool,
    message: str,
) -> GuardrailMetric:
    return GuardrailMetric(
        value=value,
        normal_range=normal_range,
        **{"pass": passed},
        message=message,
    )


def build_fragmentomics_guardrails(
    metrics: FragmentomicsMetrics,
    cfg: FragmentomicsConfig,
) -> FragmentomicsGuardrailDetails:
    """cfDNA-specific guardrails (parallel to WGBS insert-size rule)."""
    med_min = int(cfg.median_insert_min_bp)
    med_max = int(cfg.median_insert_max_bp)
    median_pass = med_min <= metrics.median_insert_size <= med_max

    peak = metrics.nucleosome_peak_bp
    peak_lo = int(cfg.nucleosome_peak_bp_min)
    peak_hi = int(cfg.nucleosome_peak_bp_max)
    peak_pass = peak is not None and peak_lo <= peak <= peak_hi

    short_max_frac = float(cfg.max_short_fragment_fraction or 0.35)
    short_pass = metrics.short_fragment_fraction <= short_max_frac

    return FragmentomicsGuardrailDetails(
        median_insert_bp=_guardrail_metric(
            value=float(metrics.median_insert_size),
            normal_range=f"{med_min}-{med_max} bp",
            passed=median_pass,
            message=(
                "Median insert size within cfDNA-typical range."
                if median_pass
                else f"Median insert {metrics.median_insert_size} bp outside cfDNA range {med_min}-{med_max}."
            ),
        ),
        nucleosome_peak_bp=_guardrail_metric(
            value=float(peak if peak is not None else -1),
            normal_range=f"{peak_lo}-{peak_hi} bp (histogram mode)",
            passed=peak_pass,
            message=(
                f"Nucleosome peak at {peak} bp within expected window."
                if peak_pass
                else f"Nucleosome peak {peak} bp outside {peak_lo}-{peak_hi}."
            ),
        ),
        short_fragment_fraction=_guardrail_metric(
            value=metrics.short_fragment_fraction,
            normal_range=f"<= {short_max_frac:.2f}",
            passed=short_pass,
            message=(
                "Short-fragment fraction within limit."
                if short_pass
                else (
                    f"Short-fragment fraction {metrics.short_fragment_fraction:.3f} "
                    f"exceeds {short_max_frac:.2f} (fragments <= {cfg.short_fragment_max_bp} bp)."
                )
            ),
        ),
    )


def apply_fragmentomics_to_payload(
    payload: Dict[str, Any],
    cfg: Optional[FragmentomicsConfig],
) -> None:
    """
    Attach fragmentomics_metrics and optional guardrails.details.fragmentomics in-place.
    """
    if cfg is None or not cfg.is_active():
        return

    metrics = compute_fragmentomics_metrics(payload, cfg)
    payload["fragmentomics_metrics"] = metrics.model_dump(mode="python", by_alias=True)

    profile = cfg.resolved_profile()
    if profile != "cfdna":
        return

    frag_guard = build_fragmentomics_guardrails(metrics, cfg)
    guardrails = payload.get("guardrails")
    if not isinstance(guardrails, dict):
        return

    details = guardrails.get("details")
    if not isinstance(details, dict):
        details = {}
        guardrails["details"] = details

    details["fragmentomics"] = frag_guard.model_dump(mode="python", by_alias=True)

    frag_pass = all(
        m.get("pass", m.get("passed", False))
        for m in details["fragmentomics"].values()
        if isinstance(m, dict)
    )
    if not frag_pass:
        guardrails["overall_pass"] = False
        rec = str(guardrails.get("recommendation") or "")
        if "fragmentomics" not in rec.lower():
            guardrails["recommendation"] = (
                f"{rec} cfDNA fragmentomics guardrails failed.".strip()
            )


def resolve_fragmentomics_config(
    step_cfg: Optional[Dict[str, Any]],
    *,
    project_regulatory: Optional[Dict[str, Any]] = None,
) -> Optional[FragmentomicsConfig]:
    """
    Build FragmentomicsConfig from alignment_qc step config.

    Prefer merged project config from ``ProjectConfig.get_step_config`` (analyte
    profiles already applied). The ``auto_profile_from_analyte`` branch remains
    for callers that pass raw step JSON without profile merge.
    """
    if not step_cfg:
        step_cfg = {}
    raw = step_cfg.get("fragmentomics")
    if raw is None and not step_cfg.get("auto_profile_from_analyte"):
        return None

    merged: Dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    if not merged and step_cfg.get("auto_profile_from_analyte") and project_regulatory:
        analyte = str(project_regulatory.get("primary_analyte") or "").strip().lower()
        if analyte in {"cfdna", "cf_dna", "cell_free_dna", "plasma", "plasma_cfdna"}:
            merged.setdefault("enabled", True)
            merged.setdefault("profile", "cfdna")

    if not merged:
        return None
    try:
        cfg = FragmentomicsConfig.model_validate(merged)
    except Exception:
        return None
    return cfg if cfg.is_active() else None
