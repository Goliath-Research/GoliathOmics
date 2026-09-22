"""
Summarize alignment_qc and methyl-fragmentomics artifacts for readiness / Grok.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional

from methyl_utils.action_config_resolver import resolve_action_config_from_env


def _regulatory_from_project_dict(production_project: Dict[str, Any]) -> Dict[str, Any]:
    reg = production_project.get("regulatory")
    return dict(reg) if isinstance(reg, dict) else {}


def _resolve_action_from_project_dict(
    production_project: Dict[str, Any],
    action_key: str,
) -> Dict[str, Any]:
    return resolve_action_config_from_env(
        action_key,
        regulatory=_regulatory_from_project_dict(production_project),
    )


def _safe_read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _normalize_analyte(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    s = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if s in {"cfdna", "cf_dna", "cell_free_dna"}:
        return "cfdna"
    return s or None


def summarize_alignment_qc_fragmentomics(alignment_qc_dir: Path) -> Dict[str, Any]:
    """Aggregate fragmentomics_metrics from per-sample alignment_qc JSON files."""
    alignment_qc_dir = Path(alignment_qc_dir)
    if not alignment_qc_dir.is_dir():
        return {
            "alignment_qc_dir_present": False,
            "n_samples_with_metrics": 0,
            "samples": [],
        }

    samples: List[Dict[str, Any]] = []
    for path in sorted(alignment_qc_dir.glob("*.json")):
        payload = _safe_read_json(path)
        if not payload:
            continue
        fm = payload.get("fragmentomics_metrics")
        if not isinstance(fm, dict):
            continue
        guard_pass = None
        details = (payload.get("guardrails") or {}).get("details") or {}
        frag_g = details.get("fragmentomics")
        if isinstance(frag_g, dict):
            passes = [
                m.get("pass", m.get("passed"))
                for m in frag_g.values()
                if isinstance(m, dict)
            ]
            if passes:
                guard_pass = all(bool(p) for p in passes)
        samples.append(
            {
                "sample_id": payload.get("sample_id") or path.stem,
                "profile": fm.get("profile"),
                "median_insert_size": fm.get("median_insert_size"),
                "short_fragment_fraction": fm.get("short_fragment_fraction"),
                "nucleosome_peak_bp": fm.get("nucleosome_peak_bp"),
                "fragmentomics_guardrails_pass": guard_pass,
            }
        )

    medians = [s["median_insert_size"] for s in samples if s.get("median_insert_size") is not None]
    short_fracs = [
        s["short_fragment_fraction"]
        for s in samples
        if s.get("short_fragment_fraction") is not None
    ]
    return {
        "alignment_qc_dir_present": True,
        "n_samples_with_metrics": len(samples),
        "samples": samples[:20],
        "cohort_median_insert_size": statistics.median(medians) if medians else None,
        "cohort_mean_short_fragment_fraction": (
            round(sum(short_fracs) / len(short_fracs), 4) if short_fracs else None
        ),
        "all_fragmentomics_guardrails_pass": (
            all(s.get("fragmentomics_guardrails_pass") for s in samples if s.get("fragmentomics_guardrails_pass") is not None)
            if samples
            else None
        ),
    }


def summarize_bam_fragmentomics(fragmentomics_dir: Path) -> Dict[str, Any]:
    """Read fragmentomics_summary.json and per-sample sample_features.json stubs."""
    fragmentomics_dir = Path(fragmentomics_dir)
    summary_path = fragmentomics_dir / "fragmentomics_summary.json"
    summary = _safe_read_json(summary_path)
    if not summary:
        return {
            "fragmentomics_dir_present": fragmentomics_dir.is_dir(),
            "summary_present": False,
            "n_samples": 0,
        }

    sample_summaries: List[Dict[str, Any]] = []
    for sample_id in (summary.get("samples") or {}).keys():
        feat_path = fragmentomics_dir / sample_id / "sample_features.json"
        feat = _safe_read_json(feat_path) or {}
        sample_summaries.append(
            {
                "sample_id": sample_id,
                "modes": feat.get("modes") or [],
                "wps": feat.get("wps"),
                "end_motifs": feat.get("end_motifs"),
            }
        )

    return {
        "fragmentomics_dir_present": True,
        "summary_present": True,
        "schema_version": summary.get("schema_version"),
        "modes": summary.get("modes"),
        "n_samples": len(summary.get("samples") or {}),
        "samples": sample_summaries[:20],
    }


def build_fragmentomics_report(
    *,
    project_root: Path,
    production_project: Dict[str, Any],
) -> Dict[str, Any]:
    """Combine regulatory analyte, alignment_qc Phase 1, and optional BAM fragmentomics step."""
    reg = _regulatory_from_project_dict(production_project)
    analyte = _normalize_analyte(reg.get("primary_analyte"))

    frag_step = _resolve_action_from_project_dict(production_project, "fragmentomics")
    aq_step = _resolve_action_from_project_dict(production_project, "alignment_qc")

    alignment_qc_dir = project_root / "alignment_qc"
    fragmentomics_dir = project_root / "fragmentomics"
    if isinstance(frag_step, dict) and frag_step.get("output_dir"):
        fragmentomics_dir = Path(str(frag_step["output_dir"]))

    try:
        from methyl_utils import load_project

        proj_path = project_root / "monte_carlo_runs" / "production" / "project.json"
        if proj_path.is_file():
            proj = load_project(proj_path)
            paths = proj.get_derived_paths()
            alignment_qc_dir = Path(paths.alignment_qc_dir)
            if isinstance(frag_step, dict) and not frag_step.get("output_dir"):
                fragmentomics_dir = Path(paths.output_base) / "fragmentomics"
    except Exception:
        pass

    aq_summary = summarize_alignment_qc_fragmentomics(alignment_qc_dir)
    bam_summary = summarize_bam_fragmentomics(fragmentomics_dir)

    frag_enabled = bool(
        (isinstance(frag_step, dict) and frag_step.get("enabled"))
        or (isinstance(aq_step, dict) and (aq_step.get("fragmentomics") or {}).get("enabled"))
        or aq_step.get("auto_profile_from_analyte")
    )

    return {
        "primary_analyte": analyte,
        "expected_for_analyte": analyte == "cfdna",
        "fragmentomics_step_enabled": bool(isinstance(frag_step, dict) and frag_step.get("enabled")),
        "alignment_qc_fragmentomics": aq_summary,
        "bam_fragmentomics": bam_summary,
        "configured": frag_enabled or analyte == "cfdna",
    }
