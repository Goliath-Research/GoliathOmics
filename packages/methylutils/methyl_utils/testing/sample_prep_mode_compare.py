"""Linear vs ``pangenome_wgbs`` SamplePrep experiment helpers (no DB).

Layout contract (lab / QNAP shape) — legacy mode trees:

```text
/work/samples/<sampleId>/
  <sampleId>_1.fastq.gz          # shared root FASTQs
  <sampleId>_2.fastq.gz
  linear/                        # experiment-only mode tree
  pangenome_wgbs/                # experiment-only mode tree
```

Canonical **comparison arms** (coexist with mojo-align IDs):

```text
align.linear.parabricks/
align.linear.mojo/
align.pangenome_wgbs.vg/
align.pangenome_wgbs.mojo/
extract.methylextractor/
extract.methyldackel/
```

Reports under ``/work/samples/_comparisons/<stamp>/``. Production SamplePrep
binds ``sampleDir`` to the same ``align.*`` leaves (see
``methyl_utils.sample_arm_layout``).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..sample_arm_layout import (
    ALIGN_ARM_TO_MODE as _ALIGN_ARM_TO_MODE,
    COMPARE_ALIGN_ARMS,
    COMPARE_EXTRACT_ARMS,
    action_config_overlay_for_arm,
    alignment_mode_for_arm,
    discover_root_fastqs,
    ensure_align_arm_dir,
    link_root_fastqs_into_align_arm,
    link_root_fastqs_into_dir,
    sample_root_dir,
)
from ..test_data_registry import SamplePrepCanaryThresholds
from .sample_prep_canary import (
    MODE_ALIGN_ACTION,
    CheckResult,
    ModeReport,
    compare_linear_vs_wgbs,
    compute_cg_overlap_stats,
    mode_report_to_dict,
    validate_mode_artifacts,
)

COMPARE_MODES: Tuple[str, ...] = ("linear", "pangenome_wgbs")

_ALIGN_ACTION_NAMES = {
    "linear": {
        "sample.parabricks_fq2bam",
        "sample.parabricks_fq2bam_meth",
        "parabricks_fq2bam",
    },
    "pangenome_wgbs": {
        "sample.methylgrapher_wgbs_align",
        "methylgrapher_wgbs_align",
    },
}


def mode_sample_dir(sample_root: Path | str, mode: str) -> Path:
    if mode not in COMPARE_MODES:
        raise ValueError(f"unsupported compare mode: {mode}")
    return Path(sample_root).expanduser().resolve() / mode


def ensure_mode_dir(sample_root: Path | str, mode: str) -> Path:
    mode_dir = mode_sample_dir(sample_root, mode)
    mode_dir.mkdir(parents=True, exist_ok=True)
    return mode_dir


def link_root_fastqs_into_mode(
    sample_root: Path | str,
    mode: str,
    *,
    sample_id: str,
    method: str = "hardlink",
) -> List[Path]:
    """Hardlink (or symlink/copy) root FASTQs into the mode sampleDir."""
    root = Path(sample_root).expanduser().resolve()
    mode_dir = ensure_mode_dir(root, mode)
    try:
        return link_root_fastqs_into_dir(
            root, mode_dir, sample_id=sample_id, method=method
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"No non-empty root FASTQs under {root} for sample_id={sample_id}; "
            "download once to the sample root (lab/QNAP layout) or pass --reuse-local-fastq "
            "only when files already exist"
        ) from exc


def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            yield row


def _action_name(row: Mapping[str, Any]) -> str:
    for key in ("action", "action_name", "actionName", "capability", "name"):
        val = row.get(key)
        if val:
            return str(val)
    return ""


def _duration_from_row(row: Mapping[str, Any]) -> Optional[float]:
    for key in ("duration_ms", "durationMs", "wall_ms"):
        val = row.get(key)
        if val is None:
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    outputs = row.get("outputs")
    if isinstance(outputs, dict):
        for key in ("duration_ms", "durationMs"):
            val = outputs.get(key)
            if val is None:
                continue
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    started = row.get("started_at_utc") or row.get("started_at")
    finished = row.get("finished_at_utc") or row.get("finished_at") or row.get("ts_utc")
    if started and finished:
        try:
            def _parse(ts: str) -> datetime:
                text = str(ts).replace("Z", "+00:00")
                return datetime.fromisoformat(text)

            delta = _parse(str(finished)) - _parse(str(started))
            ms = delta.total_seconds() * 1000.0
            if ms >= 0:
                return ms
        except (TypeError, ValueError):
            return None
    return None


def extract_alignment_duration_ms(
    sample_dir: Path | str,
    *,
    sample_id: str,
    mode: str,
    task_rows: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Optional[float]:
    """Best-effort alignment wall time from task outputs, action log, or prep log."""
    sample_dir = Path(sample_dir)
    wanted = _ALIGN_ACTION_NAMES.get(mode) or {MODE_ALIGN_ACTION.get(mode, "")}
    wanted = {w for w in wanted if w}

    candidates: List[float] = []

    def _consider(rows: Iterable[Mapping[str, Any]]) -> None:
        for row in rows:
            name = _action_name(row)
            if name not in wanted and not any(w in name for w in wanted):
                continue
            dur = _duration_from_row(row)
            if dur is not None:
                candidates.append(dur)

    if task_rows:
        _consider(task_rows)

    for log_name in (
        "action_run_log.jsonl",
        f"{sample_id}.sample_prep_log.jsonl",
        f"{sample_dir.name}.sample_prep_log.jsonl",
    ):
        _consider(_iter_jsonl(sample_dir / log_name))

    if not candidates:
        return None
    # Prefer the maximum observed align duration (covers retries).
    return max(candidates)


def optional_h5_coverage_stats(
    sample_dir: Path | str,
    *,
    depth_thresholds: Optional[Sequence[int]] = None,
    max_files: int = 8,
    max_sites_per_file: int = 2_000_000,
) -> Dict[str, Any]:
    """Top-level H5 CpG coverage summary (no full dump).

    Uses ``mC + uC`` as per-site coverage when the MethylExtractor structured
    dataset is present. Returns empty dict when h5py/files are unavailable.
    """
    sample_dir = Path(sample_dir)
    h5_files = sorted(p for p in sample_dir.glob("*-CG.h5") if p.is_file())[:max_files]
    if not h5_files:
        return {}
    try:
        import h5py  # type: ignore
        import numpy as np
    except ImportError:
        return {"h5_stats_error": "h5py_or_numpy_unavailable", "n_h5_files_seen": len(h5_files)}

    coverages: List[Any] = []
    files_read = 0
    for path in h5_files:
        try:
            with h5py.File(path, "r") as handle:
                if "methylation_data" not in handle:
                    continue
                ds = handle["methylation_data"]
                n = int(ds.shape[0]) if getattr(ds, "shape", None) else 0
                if n <= 0:
                    continue
                take = min(n, max_sites_per_file)
                chunk = ds[:take]
                if "mC" in chunk.dtype.names and "uC" in chunk.dtype.names:
                    cov = np.asarray(chunk["mC"], dtype=np.float64) + np.asarray(
                        chunk["uC"], dtype=np.float64
                    )
                    coverages.append(cov)
                    files_read += 1
        except OSError:
            continue
    if not coverages:
        return {"n_h5_files_seen": len(h5_files), "n_h5_files_read": 0}

    import numpy as np

    all_cov = np.concatenate(coverages)
    out: Dict[str, Any] = {
        "n_h5_files_seen": len(h5_files),
        "n_h5_files_read": files_read,
        "n_sites_sampled": int(all_cov.size),
        "mean_coverage": float(np.mean(all_cov)) if all_cov.size else None,
        "median_coverage": float(np.median(all_cov)) if all_cov.size else None,
    }
    if depth_thresholds:
        fracs: Dict[str, float] = {}
        for depth in depth_thresholds:
            try:
                d = int(depth)
            except (TypeError, ValueError):
                continue
            if d < 0 or all_cov.size == 0:
                continue
            fracs[str(d)] = float(np.mean(all_cov >= d))
        out["fraction_cpgs_ge_depth"] = fracs
    return out


def enrich_mode_report_timing_and_h5(
    report: ModeReport,
    *,
    thresholds: Optional[SamplePrepCanaryThresholds] = None,
    task_rows: Optional[Sequence[Mapping[str, Any]]] = None,
) -> ModeReport:
    """Attach alignment duration + optional H5 coverage stats onto a ModeReport."""
    thr = thresholds or SamplePrepCanaryThresholds()
    dur = extract_alignment_duration_ms(
        report.sample_dir,
        sample_id=report.sample_id,
        mode=report.mode,
        task_rows=task_rows,
    )
    if dur is not None:
        report.metrics["alignment_duration_ms"] = dur
    h5_stats = optional_h5_coverage_stats(
        report.sample_dir,
        depth_thresholds=thr.cpg_depth_thresholds,
    )
    if h5_stats:
        report.metrics["h5_coverage"] = h5_stats
    # After successful SamplePrep, delete_bam may remove BAM; keep extract QC as the gate.
    if report.metrics.get("extraction_qc_pass") is True or report.metrics.get("n_h5_files"):
        for check in report.checks:
            if check.name == "bam_present" and not check.ok:
                check.ok = True
                check.detail = f"{check.detail}; tolerated after delete_bam cleanup"
                break
    return report


def build_sample_compare_block(
    *,
    sample_id: str,
    linear: ModeReport,
    wgbs: ModeReport,
    thresholds: Optional[SamplePrepCanaryThresholds] = None,
) -> Dict[str, Any]:
    thr = thresholds or SamplePrepCanaryThresholds()
    checks = compare_linear_vs_wgbs(linear, wgbs, thresholds=thr)
    cov_l = linear.metrics.get("cpg_weighted_mean_coverage")
    cov_w = wgbs.metrics.get("cpg_weighted_mean_coverage")
    sites_l = linear.metrics.get("cpg_sites")
    sites_w = wgbs.metrics.get("cpg_sites")
    time_l = linear.metrics.get("alignment_duration_ms")
    time_w = wgbs.metrics.get("alignment_duration_ms")
    hypothesis = {
        "claim": (
            "pangenome_wgbs improves usable read support at CpG positions "
            "(coverage / site yield) vs linear; alignment runtime is a cost metric"
        ),
        "not_a_claim": "stock Giraffe methylation biology parity",
        "cpg_coverage_delta": None,
        "cpg_sites_delta": None,
        "alignment_time_ratio_wgbs_over_linear": None,
    }
    try:
        if cov_l is not None and cov_w is not None:
            hypothesis["cpg_coverage_delta"] = float(cov_w) - float(cov_l)
    except (TypeError, ValueError):
        pass
    try:
        if sites_l is not None and sites_w is not None:
            hypothesis["cpg_sites_delta"] = float(sites_w) - float(sites_l)
    except (TypeError, ValueError):
        pass
    try:
        if time_l and time_w and float(time_l) > 0:
            hypothesis["alignment_time_ratio_wgbs_over_linear"] = float(time_w) / float(time_l)
    except (TypeError, ValueError):
        pass

    return {
        "sample_id": sample_id,
        "ok": all(c.ok for c in linear.checks)
        and all(c.ok for c in wgbs.checks)
        and all(c.ok for c in checks),
        "linear": mode_report_to_dict(linear),
        "pangenome_wgbs": mode_report_to_dict(wgbs),
        "comparison_checks": [
            {"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks
        ],
        "hypothesis": hypothesis,
    }


def build_mode_compare_report(
    *,
    sample_blocks: Sequence[Mapping[str, Any]],
    thresholds: Optional[SamplePrepCanaryThresholds] = None,
    meta: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    overall = all(bool(b.get("ok")) for b in sample_blocks) if sample_blocks else False
    return {
        "schema_version": "1.0",
        "report_type": "sample_prep_linear_vs_pangenome_wgbs",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "overall_pass": overall,
        "layout_policy": {
            "fastq_location": "sample_root",
            "mode_subdirs": list(COMPARE_MODES),
            "mode_subdirs_are": "experiment_only_scaffolding",
            "archive": "disabled_until_after_review",
        },
        "hypothesis": (
            "pangenome_wgbs improves usable read support at CpG positions "
            "(coverage/site yield); alignment wall time is reported as cost"
        ),
        "thresholds": thresholds.model_dump(mode="python") if thresholds else None,
        "samples": list(sample_blocks),
        "meta": dict(meta or {}),
    }


def write_mode_compare_markdown(report: Mapping[str, Any], path: Path) -> None:
    lines = [
        "# Linear vs pangenome_wgbs SamplePrep comparison",
        "",
        f"- Generated: `{report.get('generated_at_utc')}`",
        f"- Overall pass: **{report.get('overall_pass')}**",
        f"- Hypothesis: {report.get('hypothesis')}",
        "",
        "## Layout policy",
        "",
        "- FASTQs stay at `/work/samples/<sampleId>/` (lab/QNAP shape; no `/fastq` child).",
        "- `linear/` and `pangenome_wgbs/` are **experiment-only** trees so both arms can coexist.",
        "- `sampleStorage` omitted — no QNAP archive until after review.",
        "- After choosing a winner, promote that arm back to the flat sample tree and archive once.",
        "",
    ]
    for sample in report.get("samples") or []:
        sid = sample.get("sample_id")
        lines.append(f"## Sample `{sid}`")
        lines.append("")
        lines.append(f"- ok: `{sample.get('ok')}`")
        hyp = sample.get("hypothesis") or {}
        lines.append(
            f"- CpG coverage Δ (wgbs − linear): `{hyp.get('cpg_coverage_delta')}`"
        )
        lines.append(f"- CpG sites Δ (wgbs − linear): `{hyp.get('cpg_sites_delta')}`")
        lines.append(
            f"- Alignment time ratio (wgbs/linear): "
            f"`{hyp.get('alignment_time_ratio_wgbs_over_linear')}`"
        )
        lines.append("")
        for arm in ("linear", "pangenome_wgbs"):
            mode = sample.get(arm) or {}
            lines.append(f"### {arm}")
            lines.append("")
            lines.append(f"- sample_dir: `{mode.get('sample_dir')}`")
            metrics = mode.get("metrics") or {}
            focus = {
                k: metrics.get(k)
                for k in (
                    "mapping_rate",
                    "duplication_rate",
                    "cpg_weighted_mean_coverage",
                    "cpg_sites",
                    "alignment_duration_ms",
                    "extraction_qc_pass",
                    "h5_coverage",
                )
                if k in metrics
            }
            if focus:
                lines.append(f"- metrics: `{json.dumps(focus, sort_keys=True, default=str)}`")
            lines.append("")
        if sample.get("comparison_checks"):
            lines.append("| Check | OK | Detail |")
            lines.append("|-------|----|--------|")
            for check in sample.get("comparison_checks") or []:
                lines.append(
                    f"| {check.get('name')} | {check.get('ok')} | {check.get('detail')} |"
                )
            lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_compare_arm(
    *,
    mode: str,
    sample_id: str,
    sample_dir: Path,
    thresholds: Optional[SamplePrepCanaryThresholds] = None,
    observed_actions: Optional[Sequence[str]] = None,
    task_rows: Optional[Sequence[Mapping[str, Any]]] = None,
) -> ModeReport:
    report = validate_mode_artifacts(
        mode=mode,
        sample_id=sample_id,
        sample_dir=sample_dir,
        thresholds=thresholds,
        observed_actions=observed_actions,
    )
    return enrich_mode_report_timing_and_h5(
        report, thresholds=thresholds, task_rows=task_rows
    )


def build_start_payload(
    *,
    sample_id: str,
    mode: str,
    sample_dir: Path,
    project_path: Path | str,
    workflow_version_id: Optional[int],
    primary_analyte: str,
    reference_fasta: str,
    fastq_storage: Mapping[str, Any],
    fastq_prefix: str = "",
    reference_gtf: Optional[str] = None,
    library_protocol: Optional[str] = None,
    pipeline_procedure: Optional[str] = None,
    action_config: Optional[Mapping[str, Any]] = None,
    program_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a sample-prep-start body for one experiment arm (no sampleStorage)."""
    body: Dict[str, Any] = {
        "projectPath": str(project_path),
        "alignmentMode": mode,
        "primaryAnalyte": primary_analyte,
        "referenceFasta": reference_fasta,
        "deleteFastqs": False,
        # Keep mode trees local: do not fill portal sampleStorage / QNAP archive.
        "disableArchive": True,
        "fastqStorage": dict(fastq_storage),
        "samples": [
            {
                "sampleId": sample_id,
                "sampleDir": str(sample_dir),
                # Explicit "." keeps file sources rooted at sampleDir (lab FASTQs are
                # hardlinked there). Empty string is treated as unset and becomes sampleId/.
                "fastqPrefix": fastq_prefix if fastq_prefix else ".",
            }
        ],
    }
    if reference_gtf:
        body["referenceGtf"] = str(reference_gtf)
    if program_path:
        # Prefer compiling the current DomainProgram when DB SamplePrep is stale.
        body["program_path"] = str(program_path)
    elif workflow_version_id is not None:
        body["workflow_version_id"] = int(workflow_version_id)
    if library_protocol:
        body["libraryProtocol"] = library_protocol
    if pipeline_procedure:
        body["pipelineProcedure"] = pipeline_procedure
    if action_config:
        body["actionConfig"] = dict(action_config)
    # Intentionally omit sampleStorage / sampleDestination (no QNAP archive).
    return body


# Re-exported production helper (bakeoff wrappers stay import-stable).


def ensure_comparison_arms(
    sample_root: Path | str,
    *,
    sample_id: str,
    arms: Optional[Sequence[str]] = None,
    link_fastqs: bool = True,
) -> Dict[str, Path]:
    """Create coexisting align.* (and optional extract.*) dirs for a sample."""
    root = Path(sample_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    selected = list(arms) if arms else list(COMPARE_ALIGN_ARMS)
    out: Dict[str, Path] = {}
    for arm in selected:
        if arm in COMPARE_EXTRACT_ARMS:
            out[arm] = ensure_align_arm_dir(root, arm)
            continue
        out[arm] = ensure_align_arm_dir(root, arm)
        if link_fastqs and arm in _ALIGN_ARM_TO_MODE:
            link_root_fastqs_into_align_arm(root, arm, sample_id=sample_id)
    return out


def build_align_arm_start_payload(
    *,
    sample_id: str,
    arm: str,
    sample_root: Path | str,
    project_path: Path | str,
    workflow_version_id: Optional[int],
    primary_analyte: str,
    reference_fasta: str,
    fastq_storage: Mapping[str, Any],
    fastq_prefix: str = "",
    reference_gtf: Optional[str] = None,
    library_protocol: Optional[str] = None,
    pipeline_procedure: Optional[str] = None,
    action_config: Optional[Mapping[str, Any]] = None,
    program_path: Optional[str] = None,
) -> Dict[str, Any]:
    """sample-prep-start body for one ``align.*`` arm (explicit engine overlay)."""
    mode = alignment_mode_for_arm(arm)
    sample_dir = ensure_align_arm_dir(sample_root, arm)
    link_root_fastqs_into_align_arm(sample_root, arm, sample_id=sample_id)
    overlay = action_config_overlay_for_arm(arm)
    merged: Dict[str, Any] = dict(action_config or {})
    for key, slice_cfg in overlay.items():
        base = dict(merged.get(key) or {})
        base.update(slice_cfg)
        merged[key] = base
    return build_start_payload(
        sample_id=sample_id,
        mode=mode,
        sample_dir=sample_dir,
        project_path=project_path,
        workflow_version_id=workflow_version_id,
        primary_analyte=primary_analyte,
        reference_fasta=reference_fasta,
        fastq_storage=fastq_storage,
        fastq_prefix=fastq_prefix,
        reference_gtf=reference_gtf,
        library_protocol=library_protocol,
        pipeline_procedure=pipeline_procedure,
        action_config=merged,
        program_path=program_path,
    )


def comparisons_root(samples_base: Path | str = "/work/samples") -> Path:
    return Path(samples_base).expanduser().resolve() / "_comparisons"


def new_comparison_stamp(*, when: Optional[datetime] = None) -> str:
    ts = when or datetime.now(timezone.utc)
    return ts.strftime("%Y%m%dT%H%M%SZ")


def ensure_comparison_report_dir(
    samples_base: Path | str = "/work/samples",
    *,
    stamp: Optional[str] = None,
) -> Path:
    """Create ``/work/samples/_comparisons/<stamp>/`` and refresh ``latest`` symlink."""
    stamp = stamp or new_comparison_stamp()
    root = comparisons_root(samples_base)
    out = root / stamp
    out.mkdir(parents=True, exist_ok=True)
    latest = root / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    try:
        os.symlink(out.name, latest)
    except OSError:
        # Cross-device or permission: leave stamp dir without latest link.
        pass
    return out


def write_comparison_arms_report(
    report_dir: Path | str,
    *,
    sample_id: str,
    arms: Mapping[str, Mapping[str, Any]],
    gates: Optional[Mapping[str, Any]] = None,
    notes: Optional[Sequence[str]] = None,
    methyldackel: Optional[Mapping[str, Any]] = None,
) -> Tuple[Path, Path]:
    """Write comparison.json + comparison.md for a multi-arm bakeoff.

    ``arms`` maps align-arm id → metrics dict (wall_s, mapping_rate, …).
    """
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "schema": "methylpipeline.comparison_arms",
        "schema_version": "1.0.0",
        "sample_id": sample_id,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "arms": dict(arms),
        "gates": dict(gates or {}),
        "notes": list(notes or []),
        "originals_retained": [
            "Clara fq2bam_meth (align.linear.parabricks)",
            "vg giraffe cpu_vg (align.pangenome_wgbs.vg)",
            "optional MethylDackel (extract.methyldackel)",
        ],
    }
    if methyldackel is not None:
        payload["extract_methyldackel"] = dict(methyldackel)

    json_path = report_dir / "comparison.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        f"# Comparison arms — `{sample_id}`",
        "",
        f"Generated: `{payload['generated_at_utc']}`",
        "",
        "Original tools (Clara / vg / optional MethylDackel) are retained for before/after.",
        "",
        "## Arms",
        "",
        "| Arm | Key metrics |",
        "|-----|-------------|",
    ]
    for arm, metrics in sorted(arms.items()):
        focus = {
            k: metrics.get(k)
            for k in (
                "wall_s",
                "mapping_rate",
                "duplication_rate",
                "engine",
                "align_engine",
                "bam",
                "status",
            )
            if k in metrics
        }
        lines.append(f"| `{arm}` | `{json.dumps(focus, sort_keys=True, default=str)}` |")
    lines.append("")
    if gates:
        lines.append("## Gates")
        lines.append("")
        lines.append("| Gate | Status | Detail |")
        lines.append("|------|--------|--------|")
        for name, info in gates.items():
            if isinstance(info, Mapping):
                lines.append(
                    f"| {name} | {info.get('status', '')} | {info.get('detail', '')} |"
                )
            else:
                lines.append(f"| {name} | {info} | |")
        lines.append("")
    if methyldackel is not None:
        lines.append("## MethylDackel extract A/B (optional)")
        lines.append("")
        lines.append(f"```json\n{json.dumps(methyldackel, indent=2, sort_keys=True)}\n```")
        lines.append("")
    if notes:
        lines.append("## Notes")
        lines.append("")
        for note in notes:
            lines.append(f"- {note}")
        lines.append("")
    md_path = report_dir / "comparison.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path
