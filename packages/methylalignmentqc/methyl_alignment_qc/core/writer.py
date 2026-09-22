"""
Write per-sample slim V2.1 guardrail-summary JSON files.
"""

import json
import math
import os
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from ..models.sample_qc import ExportedSampleQCPayload, ParabricksMetricsPayload
from ..models.sample_qc_v2 import ExportedSampleQCV2Payload
from ..utils.v1_to_v2_migration import v1_model_to_v2
from . import parser as core_parser
from ..models.config import (
    AlignmentGuardrailsConfig,
    BisulfiteConversionConfig,
    CoreGuardrailsConfig,
    CycleScreeningConfig,
    FragmentomicsConfig,
    OptionalGuardrailsConfig,
)
from .alignment_derived_qc import apply_alignment_derived_guardrails, compute_alignment_stats
from .bisulfite_conversion import apply_bisulfite_conversion_to_payload, nested_guardrail_group_failed
from .bam_flagstat import apply_flagstat_guardrails, run_flagstat
from .cycle_quality_screening import (
    apply_screening_recommendations,
    failed_guardrail_keys,
    screen_cycle_quality,
)
from .fragmentomics import apply_fragmentomics_to_payload
from .metrics_family import (
    MetricsFamily,
    detect_metrics_family,
    is_mojo_linear_metrics_payload,
    peek_linear_metrics_json,
)
from .mojo_linear_qc import build_mojo_linear_guardrail_report
from .qc_write_context import QcWriteContext
from .wgbs_pangenome_qc import build_wgbs_pangenome_guardrail_report
from .wgbs_parabricks_qc import apply_optional_guardrails, check_wgbs_guardrails


def write_sample_qc_json(metrics: Dict[str, Any], output_path: Path) -> None:
    """
    Write one sample's metrics dict to a JSON file.
    Optionally include summary_stats if present in metrics.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(metrics, f, indent=2)


def _find_parabricks_metrics_json(sample_dir: Path, sample_name: str) -> Optional[Path]:
    """
    Resolve canonical per-sample Parabricks JSON used for export and guardrails.

    We use the known file contract: {sample_dir}/{sample_name}.json.
    """
    direct_candidate = sample_dir / f"{sample_name}.json"
    return direct_candidate if direct_candidate.exists() else None


def _try_load_parabricks_metrics_payload(
    sample_dir: Path,
    sample_name: str,
) -> Optional[tuple[Dict[str, Any], Path]]:
    """
    Try to load Parabricks/Picard metrics for QC export.

    Prefers a full {sample_id}.json when it validates; otherwise falls back to
    {sample_id}.qc-metrics.tar (legacy folders may contain guardrails-only JSON stubs).
    Returns None when neither source yields a valid Parabricks payload.
    """
    json_path = _find_parabricks_metrics_json(sample_dir, sample_name)
    if json_path is not None:
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                raw_payload = json.load(f)
            # Reject guardrails-only stubs left by prior failed QC runs
            if isinstance(raw_payload, dict) and "quality_yield" not in raw_payload:
                raise ValueError("stub JSON without quality_yield")
            payload = ParabricksMetricsPayload.model_validate(raw_payload).model_dump(
                mode="python",
                by_alias=True,
                exclude_none=True,
            )
            if payload.get("quality_yield") is None:
                raise ValueError("Parabricks payload missing quality_yield")
            return payload, json_path
        except Exception:
            pass

    parsed_from_tar = _build_parabricks_payload_from_qc_tar(sample_dir, sample_name)
    if parsed_from_tar is None:
        return None
    payload = ParabricksMetricsPayload.model_validate(parsed_from_tar).model_dump(
        mode="python",
        by_alias=True,
        exclude_none=True,
    )
    tar_path = _find_qc_metrics_tar(sample_dir, sample_name)
    if tar_path is None:
        return None
    return payload, tar_path


def _load_parabricks_metrics_payload(
    sample_dir: Path,
    sample_name: str,
) -> tuple[Dict[str, Any], Path]:
    """Load Parabricks/Picard metrics or raise (linear/pangenome path)."""
    loaded = _try_load_parabricks_metrics_payload(sample_dir, sample_name)
    if loaded is None:
        raise RuntimeError(
            f"Missing Parabricks metrics for {sample_name}: expected a full "
            f"{sample_dir / f'{sample_name}.json'} or "
            f"{sample_dir / f'{sample_name}.qc-metrics.tar'}"
        )
    return loaded


def _find_qc_metrics_tar(sample_dir: Path, sample_name: str) -> Optional[Path]:
    """Resolve canonical qc-metrics tar path for sample.

    Prefers ``{sample}.qc-metrics.tar`` at the product path, then a unique tar
    in the sample dir, then a CAAS blob, then packs an unpacked ``.qc-metrics``
    directory left behind after a harvest that skipped product relink.
    """
    candidate = sample_dir / f"{sample_name}.qc-metrics.tar"
    if candidate.exists():
        return candidate

    tars = sorted(sample_dir.glob("*.qc-metrics.tar"))
    if len(tars) == 1:
        return tars[0]

    caas_tars = sorted(
        sample_dir.glob(f".caas/*/*/{sample_name}.qc-metrics.tar"),
        key=lambda path: path.stat().st_mtime if path.is_file() else 0,
        reverse=True,
    )
    for blob in caas_tars:
        if blob.is_file() and not blob.is_symlink():
            try:
                if candidate.exists() or candidate.is_symlink():
                    candidate.unlink()
                rel = os.path.relpath(str(blob.resolve()), start=str(candidate.parent.resolve()))
                candidate.symlink_to(rel)
                if candidate.exists():
                    return candidate
            except OSError:
                return blob

    packed = _package_qc_metrics_dir(sample_dir, sample_name)
    if packed is not None:
        return packed
    return None


def _package_qc_metrics_dir(sample_dir: Path, sample_name: str) -> Optional[Path]:
    """Pack ``{sample}.qc-metrics/`` into the product tar when the tar is missing."""
    metrics_dir = sample_dir / f"{sample_name}.qc-metrics"
    tar_path = sample_dir / f"{sample_name}.qc-metrics.tar"
    if tar_path.is_file():
        return tar_path
    if not metrics_dir.is_dir():
        return None
    yield_txt = metrics_dir / "quality_yield.txt"
    if not yield_txt.is_file():
        nested = list(metrics_dir.glob("**/quality_yield.txt"))
        if not nested:
            return None
    try:
        with tarfile.open(tar_path, "w") as tar:
            tar.add(metrics_dir, arcname=metrics_dir.name)
    except OSError:
        return None
    return tar_path if tar_path.is_file() else None


def _extract_table_rows_from_tar(tar: tarfile.TarFile, suffix: str) -> List[Dict[str, str]]:
    """Extract tabular rows from one text file inside qc-metrics tar by suffix."""
    member = next((m for m in tar.getnames() if m.endswith(suffix)), None)
    if member is None:
        return []
    raw = tar.extractfile(member)
    if raw is None:
        return []
    lines = raw.read().decode("utf-8", errors="ignore").splitlines()
    header: Optional[List[str]] = None
    rows: List[Dict[str, str]] = []
    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("##") or line.lstrip().startswith("#"):
            continue
        if "\t" not in line:
            continue
        parts = line.split("\t")
        if header is None:
            header = parts
            continue
        if len(parts) < len(header):
            continue
        rows.append({header[i]: parts[i] for i in range(len(header))})
    return rows


def _to_int(value: str) -> int:
    return int(float(value))


def _to_float(value: str) -> float:
    return float(value)


def _build_parabricks_payload_from_qc_tar(sample_dir: Path, sample_name: str) -> Optional[Dict[str, Any]]:
    """Build full Parabricks-like payload from qc-metrics tar text files."""
    tar_path = _find_qc_metrics_tar(sample_dir, sample_name)
    if tar_path is None:
        return None

    with tarfile.open(tar_path, "r") as tar:
        quality_yield_rows = _extract_table_rows_from_tar(tar, "quality_yield.txt")
        mean_quality_rows = _extract_table_rows_from_tar(tar, "mean_quality_by_cycle.txt")
        quality_score_rows = _extract_table_rows_from_tar(tar, "qualityscore.txt")
        base_dist_rows = _extract_table_rows_from_tar(tar, "base_distribution_by_cycle.txt")
        gc_summary_rows = _extract_table_rows_from_tar(tar, "gcbias_summary.txt")
        gc_detail_rows = _extract_table_rows_from_tar(tar, "gcbias_detail.txt")
        insert_rows = _extract_table_rows_from_tar(tar, "insert_size.txt")
        error_rows = _extract_table_rows_from_tar(tar, "sequencingArtifact.error_summary_metrics.txt")
        pre_adapter_rows = _extract_table_rows_from_tar(tar, "sequencingArtifact.pre_adapter_summary_metrics.txt")
        bait_bias_rows = _extract_table_rows_from_tar(tar, "sequencingArtifact.bait_bias_summary_metrics.txt")

    if not quality_yield_rows:
        return None

    qy = quality_yield_rows[0]

    # Insert-size histogram is in the same file but in a separate section; parse manually.
    insert_hist_insert_size: List[int] = []
    insert_hist_counts: List[int] = []
    with tarfile.open(tar_path, "r") as tar:
        member = next((m for m in tar.getnames() if m.endswith("insert_size.txt")), None)
        if member is not None:
            raw = tar.extractfile(member)
            if raw is not None:
                lines = raw.read().decode("utf-8", errors="ignore").splitlines()
                in_hist = False
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("## HISTOGRAM"):
                        in_hist = True
                        continue
                    if not in_hist or line.startswith("#") or line.startswith("##"):
                        continue
                    if line.startswith("insert_size\tAll_Reads.fr_count"):
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        insert_hist_insert_size.append(_to_int(parts[0]))
                        insert_hist_counts.append(_to_int(parts[1]))

    payload: Dict[str, Any] = {
        "sample_id": sample_name,
        "quality_yield": {
            "total_reads": _to_int(qy["TOTAL_READS"]),
            "pf_reads": _to_int(qy["PF_READS"]),
            "total_bases": _to_int(qy["TOTAL_BASES"]),
            "pf_bases": _to_int(qy["PF_BASES"]),
            "q20_bases": _to_int(qy["Q20_BASES"]),
            "pf_q20_bases": _to_int(qy["PF_Q20_BASES"]),
            "q30_bases": _to_int(qy["Q30_BASES"]),
            "pf_q30_bases": _to_int(qy["PF_Q30_BASES"]),
            "q20_equivalent_yield": _to_int(qy["Q20_EQUIVALENT_YIELD"]),
            "pf_q20_equivalent_yield": _to_int(qy["PF_Q20_EQUIVALENT_YIELD"]),
        },
        "mean_quality_by_cycle": {
            "cycle": [_to_int(r["CYCLE"]) for r in mean_quality_rows],
            "mean_quality": [_to_float(r["MEAN_QUALITY"]) for r in mean_quality_rows],
        },
        "quality_score_distribution": {
            "Q": [_to_int(r.get("QUALITY", r.get("Q", "0"))) for r in quality_score_rows],
            "COUNT_OF_Q": [_to_int(r["COUNT_OF_Q"]) for r in quality_score_rows],
        },
        "base_distribution_by_cycle": {
            "cycle": [_to_int(r["CYCLE"]) for r in base_dist_rows],
            "PCT_A": [_to_float(r["PCT_A"]) for r in base_dist_rows],
            "PCT_C": [_to_float(r["PCT_C"]) for r in base_dist_rows],
            "PCT_G": [_to_float(r["PCT_G"]) for r in base_dist_rows],
            "PCT_T": [_to_float(r["PCT_T"]) for r in base_dist_rows],
            "PCT_N": [_to_float(r["PCT_N"]) for r in base_dist_rows],
        },
        "gc_bias_summary": {
            "at_dropout": _to_float(gc_summary_rows[0]["AT_DROPOUT"]) if gc_summary_rows else 0.0,
            "gc_dropout": _to_float(gc_summary_rows[0]["GC_DROPOUT"]) if gc_summary_rows else 0.0,
        },
        "gc_bias_details": {
            "GC": [_to_int(r["GC"]) for r in gc_detail_rows],
            "WINDOWS": [_to_int(r["WINDOWS"]) for r in gc_detail_rows],
            "READ_STARTS": [_to_int(r["READ_STARTS"]) for r in gc_detail_rows],
            "MEAN_BASE_QUALITY": [_to_float(r["MEAN_BASE_QUALITY"]) for r in gc_detail_rows],
            "NORMALIZED_COVERAGE": [_to_float(r["NORMALIZED_COVERAGE"]) for r in gc_detail_rows],
            "ERROR_BAR": [_to_float(r["ERROR_BAR_WIDTH"]) for r in gc_detail_rows],
        },
        "insert_size_metrics": {
            "median_insert_size": _to_int(insert_rows[0]["MEDIAN_INSERT_SIZE"]) if insert_rows else 0,
            "mode_insert_size": _to_int(insert_rows[0]["MODE_INSERT_SIZE"]) if insert_rows else 0,
            "mean_insert_size": _to_float(insert_rows[0]["MEAN_INSERT_SIZE"]) if insert_rows else 0.0,
            "standard_deviation": _to_float(insert_rows[0]["STANDARD_DEVIATION"]) if insert_rows else 0.0,
            "read_pairs": _to_int(insert_rows[0]["READ_PAIRS"]) if insert_rows else 0,
            "width_of_10_percent": _to_int(insert_rows[0]["WIDTH_OF_10_PERCENT"]) if insert_rows else 0,
            "width_of_20_percent": _to_int(insert_rows[0]["WIDTH_OF_20_PERCENT"]) if insert_rows else 0,
            "width_of_30_percent": _to_int(insert_rows[0]["WIDTH_OF_30_PERCENT"]) if insert_rows else 0,
            "width_of_40_percent": _to_int(insert_rows[0]["WIDTH_OF_40_PERCENT"]) if insert_rows else 0,
            "width_of_50_percent": _to_int(insert_rows[0]["WIDTH_OF_50_PERCENT"]) if insert_rows else 0,
            "width_of_60_percent": _to_int(insert_rows[0]["WIDTH_OF_60_PERCENT"]) if insert_rows else 0,
            "width_of_70_percent": _to_int(insert_rows[0]["WIDTH_OF_70_PERCENT"]) if insert_rows else 0,
            "width_of_80_percent": _to_int(insert_rows[0]["WIDTH_OF_80_PERCENT"]) if insert_rows else 0,
            "width_of_90_percent": _to_int(insert_rows[0]["WIDTH_OF_90_PERCENT"]) if insert_rows else 0,
            "width_of_95_percent": _to_int(insert_rows[0]["WIDTH_OF_95_PERCENT"]) if insert_rows else 0,
            "width_of_99_percent": _to_int(insert_rows[0]["WIDTH_OF_99_PERCENT"]) if insert_rows else 0,
        },
        "insert_size_histogram": {
            "insert_size": insert_hist_insert_size,
            "pair_orientation": ["FR"] * len(insert_hist_insert_size),
            "All_Reads.fr_count": insert_hist_counts,
            "VALUE": [],
            "all_sets": [],
            "optical_sets": [],
            "non_optical_sets": [],
        },
        "error_summaries": {
            "REF": [r["REF_BASE"] for r in error_rows],
            "ALT": [r["ALT_BASE"] for r in error_rows],
            "COUNT": [_to_int(r["ALT_COUNT"]) for r in error_rows],
            "RATE": [_to_float(r["SUBSTITUTION_RATE"]) for r in error_rows],
            "QSCORE": [
                int(round(-10.0 * math.log10(max(_to_float(r["SUBSTITUTION_RATE"]), 1e-12)))) for r in error_rows
            ],
        },
        "pre_adapter_summaries": {
            "ARTIFACT_NAME": [r["ARTIFACT_NAME"] for r in pre_adapter_rows],
            "TOTAL_QSCORE": [_to_int(r["TOTAL_QSCORE"]) for r in pre_adapter_rows],
            "WORST_CXT": [r["WORST_CXT"] for r in pre_adapter_rows],
            "WORST_CXT_QSCORE": [_to_int(r["WORST_CXT_QSCORE"]) for r in pre_adapter_rows],
        },
        "bait_bias_summaries": {
            "ARTIFACT_NAME": [r["ARTIFACT_NAME"] for r in bait_bias_rows],
            "TOTAL_QSCORE": [_to_int(r["TOTAL_QSCORE"]) for r in bait_bias_rows],
            "WORST_CXT": [r["WORST_CXT"] for r in bait_bias_rows],
            "WORST_CXT_QSCORE": [_to_int(r["WORST_CXT_QSCORE"]) for r in bait_bias_rows],
        },
        "conversion_log": {
            "program": "Parabricks",
            "version": "unknown",
            "start_time": "unknown",
            "end_time": "unknown",
            "total_time": "unknown",
        },
    }
    return payload


def _normalize_duplication_histogram(histogram: Any) -> Dict[str, Any]:
    """Normalize histogram to columnar schema expected by exported model."""
    if isinstance(histogram, dict):
        return histogram
    if isinstance(histogram, list):
        bins: List[float] = []
        values: List[float] = []
        for row in histogram:
            if not isinstance(row, dict):
                continue
            bins.append(float(row.get("BIN")))
            values.append(float(row.get("VALUE")))
        return {
            "BIN": bins,
            "VALUE": values,
            "all_sets": [],
            "optical_sets": [],
            "non_optical_sets": [],
        }
    return {
        "BIN": [],
        "VALUE": [],
        "all_sets": [],
        "optical_sets": [],
        "non_optical_sets": [],
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_prior_qc_history(output_path: Path) -> List[Dict[str, Any]]:
    if not output_path.is_file():
        return []
    try:
        prior = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    history = prior.get("qc_history")
    if not isinstance(history, list):
        return []
    return [entry for entry in history if isinstance(entry, dict)]


def _build_qc_attempt_record(
    *,
    write_ctx: QcWriteContext,
    guardrails: Dict[str, Any],
    screening: Dict[str, Any],
) -> Dict[str, Any]:
    trigger = write_ctx.remediation_trigger or {}
    trigger_action = None
    if write_ctx.attempt > 1:
        parts = []
        if trigger.get("trimFront2"):
            parts.append("sample.trim_fastq")
        parts.append("sample.parabricks_fq2bam")
        trigger_action = " + ".join(parts) if parts else None

    return {
        "attempt": write_ctx.attempt,
        "evaluated_at_utc": _utc_now_iso(),
        "alignment_pass": write_ctx.alignment_pass,
        "reason": write_ctx.attempt_reason,
        "trigger_disposition": trigger.get("disposition") if write_ctx.attempt > 1 else None,
        "trigger_action": trigger_action,
        "trim_front2": trigger.get("trimFront2") or screening.get("trim_front2"),
        "overall_pass": bool(guardrails.get("overall_pass")),
        "disposition": str(screening.get("disposition") or ""),
        "failed_guardrails": failed_guardrail_keys(guardrails),
        "workflow_node_key": write_ctx.workflow_node_key,
    }


def _apply_screening_and_audit(
    payload: Dict[str, Any],
    *,
    cycle_screening: Optional[CycleScreeningConfig],
    optional_guardrails: Optional[OptionalGuardrailsConfig],
    write_ctx: Optional[QcWriteContext],
    output_path: Path,
    sample_dir: Path,
    sample_name: str,
) -> None:
    guardrails = payload.setdefault("guardrails", {})
    if not isinstance(guardrails, dict):
        return

    opt = optional_guardrails or OptionalGuardrailsConfig()
    apply_optional_guardrails(
        guardrails,
        payload,
        duplication_rate_max=opt.duplication_rate_max,
        min_pf_reads=opt.min_pf_reads,
    )

    cfg = cycle_screening if cycle_screening is not None else CycleScreeningConfig()
    screening: Dict[str, Any] = {}
    mqc = payload.get("mean_quality_by_cycle") or {}
    has_cycles = bool(mqc.get("cycle") or mqc.get("rows"))
    if cfg.enabled and has_cycles:
        screening = screen_cycle_quality(payload, guardrails, cfg)
        guardrails["screening"] = screening
        apply_screening_recommendations(guardrails, screening)
    elif cfg.enabled and not has_cycles:
        # Default: no invented REALIGN_TRIM. Optional Mojo/WGBS path: conversion or
        # mapped-rate failures can request trim→realign when operator enables it.
        tf = int(cfg.fallback_trim_front or 0)
        tt = int(cfg.fallback_trim_tail or 0)
        conv = payload.get("bisulfite_conversion_metrics") or {}
        details = (guardrails.get("details") or {}) if isinstance(guardrails, dict) else {}
        conv_fail = nested_guardrail_group_failed(details, "bisulfite_conversion")
        mapped_fail = False
        bam_rate = details.get("wgbs_bam_mapped_rate") if isinstance(details, dict) else None
        if isinstance(bam_rate, dict) and bam_rate.get("pass") is False:
            mapped_fail = True
        want_remediate = bool(cfg.remediate_without_cycles) and (conv_fail or mapped_fail)
        has_trim = (tf + tt) > 0
        if want_remediate and has_trim:
            # TrimSpec is (read, end, bases) — same contract as cycle screening.
            # Per-read fronts/tails live in trim_front1/…; trim_spec names the primary cut.
            if tf > 0:
                primary_spec = {"read": 1, "end": "start", "bases": tf}
            else:
                primary_spec = {"read": 1, "end": "end", "bases": tt}
            screening = {
                "disposition": "REALIGN_TRIM",
                "quality_pattern": "NO_CYCLE_METRICS_SIGNAL",
                "read_length": 0,
                "r2_start_cycle": 0,
                "trim_front1": tf,
                "trim_tail1": tt,
                "trim_front2": tf,
                "trim_tail2": tt,
                "trim_spec": primary_spec,
                "r2_start_mean_quality": None,
                "r2_recovery_mean_quality": None,
                "dip_regions": [],
                "message": (
                    "No cycle metrics; REALIGN_TRIM from conversion/mapped-rate "
                    f"signals (conversion_fail={conv_fail}, mapped_fail={mapped_fail}"
                    f", conversion_rate_pct={conv.get('conversion_rate_pct')})."
                ),
            }
            guardrails["screening"] = screening
            apply_screening_recommendations(guardrails, screening)
        else:
            screening = {
                "disposition": "USE_CURRENT_ALIGNMENT",
                "quality_pattern": "NO_CYCLE_METRICS",
                "read_length": 0,
                "r2_start_cycle": 0,
                "trim_front1": 0,
                "trim_tail1": 0,
                "trim_front2": 0,
                "trim_tail2": 0,
                "trim_spec": None,
                "r2_start_mean_quality": None,
                "r2_recovery_mean_quality": None,
                "dip_regions": [],
                "message": "Cycle quality screening skipped: no mean_quality_by_cycle metrics.",
            }
            guardrails["screening"] = screening

    ctx = write_ctx or QcWriteContext()
    prior_history = ctx.prior_qc_history or _load_prior_qc_history(output_path)
    attempt_record = _build_qc_attempt_record(
        write_ctx=ctx,
        guardrails=guardrails,
        screening=screening,
    )
    payload["qc_history"] = prior_history + [attempt_record]

    log_path = ctx.sample_prep_log_path or str(sample_dir / f"{sample_name}.sample_prep_log.jsonl")
    payload["sample_prep_log_path"] = log_path


def build_sample_qc_v2_dict(
    sample_dir: Path,
    *,
    sample_id: Optional[str] = None,
    validate_schema: bool = True,
    fragmentomics: Optional[FragmentomicsConfig] = None,
    bisulfite_conversion: Optional[BisulfiteConversionConfig] = None,
    cycle_screening: Optional[CycleScreeningConfig] = None,
    optional_guardrails: Optional[OptionalGuardrailsConfig] = None,
    alignment_guardrails: Optional[AlignmentGuardrailsConfig] = None,
    core_guardrails: Optional[CoreGuardrailsConfig] = None,
    write_context: Optional[QcWriteContext] = None,
    output_path_for_history: Optional[Path] = None,
    dedup_metrics: Optional[Dict[str, Any]] = None,
    summary_stats: Optional[Dict[str, Any]] = None,
    force_flagstat: bool = False,
    alignment_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build one slim V2.1 guardrail-summary export dict from a sample directory.

    Picard tables are read from the sample dir, used to compute guardrails, then
    dropped from the returned payload.

    Expects Picard deduplicate metrics ({sample_id}.deduplicate_metrics.txt) and
    either Parabricks metrics (linear/pangenome) or methylGrapher provenance
    (pangenome_wgbs).

    ``sample_id`` is the artifact basename (``{sample_id}.bam``). When omitted,
    it is resolved from task-provided identity or unique artifacts — not blindly
    from ``sample_dir.name`` (which may be an experiment mode leaf like ``linear``).

    Pass ``dedup_metrics`` / ``summary_stats`` when batching to avoid re-parsing files.
    """
    from ..utils.schema_validator import validate_sample_qc_metrics

    sample_dir = Path(sample_dir)
    sample_name = core_parser.resolve_sample_artifact_id(sample_dir, sample_id)
    if dedup_metrics is None:
        parsed = core_parser.parse_metrics_from_sample_paths(
            [sample_dir], sample_id_by_path={str(sample_dir): sample_name}
        )
        if sample_name not in parsed:
            raise RuntimeError(
                f"No Picard deduplicate metrics in {sample_dir}; "
                f"expected {sample_dir / f'{sample_name}.deduplicate_metrics.txt'}"
            )
        metrics = parsed[sample_name]
        if summary_stats is None:
            summary_stats = core_parser.calculate_summary_stats(parsed).get(sample_name)
    else:
        metrics = dedup_metrics

    parabricks_loaded = _try_load_parabricks_metrics_payload(sample_dir, sample_name)
    family, _prov_path, provenance = detect_metrics_family(
        sample_dir,
        sample_name,
        alignment_mode=alignment_mode,
        parabricks_available=parabricks_loaded is not None,
    )

    if family == MetricsFamily.PARABRICKS:
        if parabricks_loaded is None:
            raise RuntimeError(
                f"Missing Parabricks metrics for {sample_name}: expected a full "
                f"{sample_dir / f'{sample_name}.json'} or "
                f"{sample_dir / f'{sample_name}.qc-metrics.tar'}"
            )
        payload, metrics_source = parabricks_loaded
    elif family == MetricsFamily.MOJO_LINEAR:
        # MojoFq2bamMeth JSON is Picard-shaped but incomplete vs Clara;
        # do not require ParabricksMetricsPayload validation.
        raw_mojo = peek_linear_metrics_json(sample_dir, sample_name)
        if raw_mojo is None or not is_mojo_linear_metrics_payload(raw_mojo):
            raise RuntimeError(
                f"Missing MojoFq2bamMeth metrics JSON for {sample_name}: "
                f"expected {sample_dir / f'{sample_name}.json'} with "
                f"metrics_source=samtools+placeholders or engine=mojo_fq2bam_meth"
            )
        payload = dict(raw_mojo)
        metrics_source = sample_dir / f"{sample_name}.json"
    else:
        # methylGrapher WGBS: never use stale linear Picard tables unless this
        # Align run explicitly recorded collectmultiplemetrics success.
        if provenance is None:
            raise RuntimeError(
                f"Missing methylGrapher alignment_metrics.json for {sample_name} in {sample_dir}"
            )
        payload = {
            "sample_id": sample_name,
            "wgbs_align_metrics": provenance,
        }
        metrics_source = _prov_path or (sample_dir / f"{sample_name}.alignment_metrics.json")
        # Optional enrichment: real Picard tables from this WGBS Align only.
        if bool(provenance.get("collectmultiplemetrics")) and parabricks_loaded is not None:
            pb_payload, pb_source = parabricks_loaded
            for key, value in pb_payload.items():
                if key in {"sample_id", "guardrails", "wgbs_align_metrics"}:
                    continue
                if value is not None and key not in payload:
                    payload[key] = value
            metrics_source = pb_source

    payload["sample_id"] = sample_name

    for key, value in metrics.items():
        if key not in payload:
            payload[key] = value

    if "duplication_histogram" in payload:
        payload["duplication_histogram"] = _normalize_duplication_histogram(payload["duplication_histogram"])
    elif "duplication_histogram" not in payload:
        payload["duplication_histogram"] = {
            "BIN": [],
            "VALUE": [],
            "all_sets": [],
            "optical_sets": [],
            "non_optical_sets": [],
        }

    if summary_stats is not None:
        payload["summary_stats"] = summary_stats

    align_cfg = alignment_guardrails or AlignmentGuardrailsConfig()
    flagstat_metrics = None
    flagstat_error: Optional[str] = None
    if align_cfg.enabled and align_cfg.flagstat_enabled:
        bam_path = sample_dir / f"{sample_name}.bam"
        if bam_path.is_file() and bam_path.stat().st_size > 0:
            try:
                flagstat_metrics = run_flagstat(sample_dir, sample_name, force=force_flagstat)
                payload["alignment_flagstat"] = flagstat_metrics.model_dump()
            except RuntimeError as exc:
                flagstat_error = str(exc)
        else:
            flagstat_error = f"BAM missing or empty for flagstat: {bam_path}"

    try:
        if family == MetricsFamily.MOJO_LINEAR:
            payload["guardrails"] = build_mojo_linear_guardrail_report(
                payload, core_guardrails=core_guardrails
            )
            payload["guardrails"]["metrics_family"] = MetricsFamily.MOJO_LINEAR.value
            # Drop incomplete Picard-shaped placeholders before export schema validate.
            qy = payload.get("quality_yield") or {}
            total_reads = int(qy.get("total_reads") or 0)
            pf_reads = int(qy.get("pf_reads") or 0)
            pf_bases = int(qy.get("pf_bases") or 0)
            pf_q30 = int(qy.get("pf_q30_bases") or 0)
            payload["quality_yield"] = {
                "total_reads": total_reads,
                "pf_reads": pf_reads,
                "total_bases": int(qy.get("total_bases") or pf_bases),
                "pf_bases": pf_bases,
                "q20_bases": int(qy.get("q20_bases") or pf_q30),
                "pf_q20_bases": int(qy.get("pf_q20_bases") or pf_q30),
                "q30_bases": int(qy.get("q30_bases") or pf_q30),
                "pf_q30_bases": pf_q30,
                "q20_equivalent_yield": int(qy.get("q20_equivalent_yield") or pf_bases),
                "pf_q20_equivalent_yield": int(qy.get("pf_q20_equivalent_yield") or pf_bases),
            }
            for drop_key in (
                "engine",
                "mapper",
                "device",
                "metrics_source",
                "placeholder_fields",
                "alignment_summary",
                "mean_quality_by_cycle",
                "gc_bias_summary",
                "insert_size_metrics",
                "pre_adapter_summaries",
            ):
                payload.pop(drop_key, None)
        elif family == MetricsFamily.PARABRICKS:
            if str(metrics_source).endswith(".json"):
                payload["guardrails"] = check_wgbs_guardrails(
                    str(metrics_source), print_report=False, core_guardrails=core_guardrails
                )
            else:
                from .wgbs_parabricks_qc import _build_wgbs_guardrail_report

                payload["guardrails"] = _build_wgbs_guardrail_report(
                    payload, core_guardrails=core_guardrails
                )
            payload["guardrails"]["metrics_family"] = MetricsFamily.PARABRICKS.value
        else:
            fs_dict = None
            if flagstat_metrics is not None:
                fs_dict = (
                    flagstat_metrics.model_dump()
                    if hasattr(flagstat_metrics, "model_dump")
                    else dict(flagstat_metrics)
                )
            # Do not apply linear min_mapping_rate to C2T-surjected WGBS QC BAMs —
            # that falsely forces REALIGN_TRIM and blocks methylgrapher_wgbs_extract.
            payload["guardrails"] = build_wgbs_pangenome_guardrail_report(
                sample_id=sample_name,
                sample_dir=sample_dir,
                provenance=provenance or {},
                flagstat=fs_dict,
                min_mapped_rate=(
                    align_cfg.wgbs_min_mapped_rate if align_cfg.enabled else None
                ),
            )
            # Optional Parabricks core votes when this WGBS Align collected a
            # complete Picard payload (quality_yield + cycle/GC/insert tables).
            if bool((provenance or {}).get("collectmultiplemetrics")) and payload.get(
                "quality_yield"
            ):
                from .wgbs_parabricks_qc import _build_wgbs_guardrail_report

                try:
                    pb_gr = _build_wgbs_guardrail_report(
                        payload, core_guardrails=core_guardrails
                    )
                except Exception:
                    pb_gr = None
                if pb_gr is not None:
                    details = payload["guardrails"].setdefault("details", {})
                    for key, metric in (pb_gr.get("details") or {}).items():
                        details[key] = metric
                    payload["guardrails"]["overall_pass"] = bool(
                        payload["guardrails"].get("overall_pass")
                    ) and bool(pb_gr.get("overall_pass"))
                    payload["guardrails"]["picard_enrichment"] = True
                    payload["guardrails"]["picard_enrichment_note"] = (
                        "Parabricks CollectMultipleMetrics on restored QC BAM; "
                        "BS chemistry may skew artifact/GC — operational screening only."
                    )
                else:
                    payload["guardrails"]["picard_enrichment"] = False
                    payload["guardrails"]["picard_enrichment_note"] = (
                        "collectmultiplemetrics flagged but Picard tables incomplete; "
                        "using WGBS provenance guardrails only."
                    )
    except Exception as e:
        raise RuntimeError(f"Failed to compute guardrails for {sample_name} from {metrics_source}: {e}") from e

    if isinstance(payload.get("guardrails"), dict):
        payload["guardrails"]["sample_id"] = sample_name

    stats = compute_alignment_stats(payload)
    if stats is not None:
        payload["alignment_stats"] = stats

    if align_cfg.enabled:
        # Pangenome WGBS QC BAMs are C2T-surjected Mojo emits: linear PE
        # properly_paired_rate and min_mapping_rate falsely fail (historic FLAG=0 /
        # missing 0x2; mapping floors belong on wgbs_min_mapped_rate). Keep
        # secondary/GC/supplementary votes when Picard tables exist.
        derived_cfg = align_cfg
        flagstat_cfg = align_cfg
        if family == MetricsFamily.METHYLGRAPHER_WGBS:
            derived_cfg = align_cfg.model_copy(
                update={
                    "min_mapping_rate": None,
                    "min_properly_paired_rate": None,
                }
            )
            flagstat_cfg = derived_cfg
        apply_alignment_derived_guardrails(
            payload["guardrails"], payload, derived_cfg
        )
        if flagstat_cfg.flagstat_enabled:
            apply_flagstat_guardrails(
                payload["guardrails"],
                flagstat_metrics,
                flagstat_cfg,
                error=flagstat_error,
            )

    # Fragmentomics / bisulfite proxies need real Parabricks insert / pre-adapter tables
    wgbs_picard = (
        family == MetricsFamily.METHYLGRAPHER_WGBS
        and bool((provenance or {}).get("collectmultiplemetrics"))
        and payload.get("quality_yield") is not None
    )
    if family == MetricsFamily.PARABRICKS or wgbs_picard:
        apply_fragmentomics_to_payload(payload, fragmentomics)
        apply_bisulfite_conversion_to_payload(payload, sample_dir, bisulfite_conversion)
    else:
        # Mojo linear placeholders and WGBS without Picard: conversion sidecars only
        apply_bisulfite_conversion_to_payload(payload, sample_dir, bisulfite_conversion)

    history_path = output_path_for_history or (sample_dir / f"{sample_name}.json")
    # Constant/placeholder cycle series must not drive REALIGN_TRIM dispositions.
    screening_cfg = cycle_screening
    if family == MetricsFamily.MOJO_LINEAR and cycle_screening is not None:
        screening_cfg = cycle_screening.model_copy(update={"enabled": False})
    _apply_screening_and_audit(
        payload,
        cycle_screening=screening_cfg,
        optional_guardrails=optional_guardrails,
        write_ctx=write_context,
        output_path=history_path,
        sample_dir=sample_dir,
        sample_name=sample_name,
    )

    if validate_schema:
        errs = validate_sample_qc_metrics(payload)
        if errs:
            raise RuntimeError(f"Validation failed for {sample_name}: " + "; ".join(errs))

    v1_model = ExportedSampleQCPayload.model_validate(payload)
    v2_model = v1_model_to_v2(v1_model)
    v2_dict = v2_model.model_dump(mode="python", by_alias=True, exclude_none=True)
    if write_context is not None:
        v2_dict.setdefault("metadata", {})["qc_attempt"] = write_context.attempt
    ExportedSampleQCV2Payload.model_validate(v2_dict)
    return v2_dict


def process_samples_to_qc_jsons(
    sample_paths: List[str],
    output_dir: str,
    validate_schema: bool = True,
    fragmentomics: Optional[FragmentomicsConfig] = None,
    bisulfite_conversion: Optional[BisulfiteConversionConfig] = None,
    cycle_screening: Optional[CycleScreeningConfig] = None,
    optional_guardrails: Optional[OptionalGuardrailsConfig] = None,
    alignment_guardrails: Optional[AlignmentGuardrailsConfig] = None,
    core_guardrails: Optional[CoreGuardrailsConfig] = None,
    write_context: Optional[QcWriteContext] = None,
    sample_id: Optional[str] = None,
    sample_id_by_path: Optional[Mapping[str, str]] = None,
    alignment_mode: Optional[str] = None,
) -> None:
    """
    Parse each sample directory and write one JSON per sample to output_dir.
    Output files: {output_dir}/{sample_id}.json (slim V2.1 guardrail summary).

    Args:
        sample_paths: List of sample directory paths
        output_dir: Output directory for JSON files
        validate_schema: If True, validate each sample's JSON structure (via utils.schema_validator)
        sample_id: Explicit sample id when processing a single path (worker ``sampleId``)
        sample_id_by_path: Optional map of sample_dir path → sample id for batches
    """
    paths = [Path(p) for p in sample_paths]
    id_map: Dict[str, str] = {}
    if sample_id_by_path:
        id_map.update({str(Path(k)): str(v) for k, v in sample_id_by_path.items() if v})
    if sample_id and len(paths) == 1:
        id_map[str(paths[0])] = str(sample_id)

    resolved_ids = {
        str(p): core_parser.resolve_sample_artifact_id(p, id_map.get(str(p))) for p in paths
    }
    parsed = core_parser.parse_metrics_from_sample_paths(paths, sample_id_by_path=resolved_ids)
    if not parsed:
        return

    sample_paths_by_id = {resolved_ids[str(p)]: p for p in paths}
    summary_by_name = core_parser.calculate_summary_stats(parsed)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    for sample_name, dedup_metrics in parsed.items():
        sample_dir = sample_paths_by_id.get(sample_name)
        if sample_dir is None:
            raise RuntimeError(f"Sample directory not found for parsed sample: {sample_name}")

        output_file = out / f"{sample_name}.json"
        v2_dict = build_sample_qc_v2_dict(
            sample_dir,
            sample_id=sample_name,
            validate_schema=validate_schema,
            fragmentomics=fragmentomics,
            bisulfite_conversion=bisulfite_conversion,
            cycle_screening=cycle_screening,
            optional_guardrails=optional_guardrails,
            alignment_guardrails=alignment_guardrails,
            core_guardrails=core_guardrails,
            write_context=write_context,
            output_path_for_history=output_file,
            dedup_metrics=dedup_metrics,
            summary_stats=summary_by_name.get(sample_name),
            alignment_mode=alignment_mode,
        )
        write_sample_qc_json(v2_dict, output_file)
