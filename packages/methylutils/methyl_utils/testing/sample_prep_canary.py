"""SamplePrep real-data canary validation and reporting (no DB dependency).

Validates per-mode artifacts after a real SamplePrep run and compares linear vs
``pangenome_wgbs`` using operator-set thresholds. Stock Giraffe (``pangenome``)
is reported as an engineering comparator only.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..test_data_registry import (
    SamplePrepCanaryConfig,
    SamplePrepCanaryFastqPair,
    SamplePrepCanaryThresholds,
    load_sample_prep_canary,
)

MODE_ALIGN_ACTION = {
    "linear": "sample.parabricks_fq2bam",
    "pangenome": "sample.parabricks_giraffe",
    "pangenome_wgbs": "sample.methylgrapher_wgbs_align",
}
MODE_EXTRACT_ACTION = {
    "linear": "sample.methyl_extract",
    "pangenome": "sample.methyl_extract",
    "pangenome_wgbs": "sample.methylgrapher_wgbs_extract",
}


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    engineering_only: bool = False


@dataclass
class ModeReport:
    mode: str
    sample_id: str
    sample_dir: Path
    checks: List[CheckResult] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    artifacts: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_fastq_pair(
    storage_root: Path,
    pair: SamplePrepCanaryFastqPair,
    *,
    require_checksums: bool = True,
) -> List[CheckResult]:
    """Verify staged FASTQ pair existence and optional SHA-256 pins."""
    checks: List[CheckResult] = []
    prefix = (pair.prefix or "").lstrip("/")
    r1_name = pair.r1_name or ""
    r2_name = pair.r2_name or ""
    r1 = storage_root / prefix / r1_name
    r2 = storage_root / prefix / r2_name
    checks.append(CheckResult("fastq_r1_exists", r1.is_file(), str(r1)))
    checks.append(CheckResult("fastq_r2_exists", r2.is_file(), str(r2)))
    if r1.is_file() and pair.r1_sha256:
        digest = _sha256(r1)
        checks.append(
            CheckResult(
                "fastq_r1_sha256",
                digest.lower() == pair.r1_sha256.lower(),
                f"expected={pair.r1_sha256} actual={digest}",
            )
        )
    elif require_checksums and pair.r1_sha256 is None:
        checks.append(
            CheckResult(
                "fastq_r1_sha256_configured",
                False,
                "r1_sha256 not set; run provision_sample_prep_canary.sh and merge checksums",
            )
        )
    if r2.is_file() and pair.r2_sha256:
        digest = _sha256(r2)
        checks.append(
            CheckResult(
                "fastq_r2_sha256",
                digest.lower() == pair.r2_sha256.lower(),
                f"expected={pair.r2_sha256} actual={digest}",
            )
        )
    elif require_checksums and pair.r2_sha256 is None:
        checks.append(
            CheckResult(
                "fastq_r2_sha256_configured",
                False,
                "r2_sha256 not set; run provision_sample_prep_canary.sh and merge checksums",
            )
        )
    return checks


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _find_first(sample_dir: Path, patterns: Sequence[str]) -> Optional[Path]:
    for pat in patterns:
        hits = sorted(sample_dir.glob(pat))
        if hits:
            return hits[0]
    return None


def _qc_metrics(qc: Mapping[str, Any]) -> Dict[str, Any]:
    summary = qc.get("summary_stats") if isinstance(qc.get("summary_stats"), dict) else {}
    guard = qc.get("guardrails") if isinstance(qc.get("guardrails"), dict) else {}
    screening = qc.get("screening") if isinstance(qc.get("screening"), dict) else {}
    out: Dict[str, Any] = {
        "overall_pass": guard.get("overall_pass"),
        "disposition": screening.get("disposition") or guard.get("disposition"),
    }
    for key in (
        "mapping_rate",
        "percent_mapped",
        "duplication_rate",
        "percent_duplication",
        "pf_reads",
        "mean_coverage",
    ):
        if key in summary:
            out[key] = summary[key]
    # Common nested locations
    metrics = qc.get("metrics") if isinstance(qc.get("metrics"), dict) else {}
    for key in ("mapping_rate", "duplication_rate", "mean_coverage"):
        if key not in out and key in metrics:
            out[key] = metrics[key]
    return out


def _count_h5(sample_dir: Path) -> List[str]:
    return sorted(p.name for p in sample_dir.glob("*-*.h5") if p.is_file())


def _extraction_manifest(sample_dir: Path, sample_id: str) -> Optional[Path]:
    direct = sample_dir / f"{sample_id}.extraction_manifest.json"
    if direct.is_file():
        return direct
    return _find_first(sample_dir, ["*.extraction_manifest.json"])


def validate_mode_artifacts(
    *,
    mode: str,
    sample_id: str,
    sample_dir: Path,
    thresholds: Optional[SamplePrepCanaryThresholds] = None,
    observed_actions: Optional[Sequence[str]] = None,
) -> ModeReport:
    """Validate QC / extract / archive artifacts for one alignment mode."""
    report = ModeReport(mode=mode, sample_id=sample_id, sample_dir=sample_dir)
    thr = thresholds or SamplePrepCanaryThresholds()

    expected_align = MODE_ALIGN_ACTION.get(mode)
    expected_extract = MODE_EXTRACT_ACTION.get(mode)
    if observed_actions is not None and expected_align:
        report.checks.append(
            CheckResult(
                "align_action_observed",
                expected_align in observed_actions,
                f"expected={expected_align} observed={list(observed_actions)}",
            )
        )
    if observed_actions is not None and expected_extract:
        report.checks.append(
            CheckResult(
                "extract_action_observed",
                expected_extract in observed_actions,
                f"expected={expected_extract} observed={list(observed_actions)}",
            )
        )

    bam = _find_first(sample_dir, [f"{sample_id}.bam", "*.bam"])
    report.checks.append(CheckResult("bam_present", bam is not None and bam.is_file(), str(bam)))
    if bam:
        report.artifacts["bamPath"] = str(bam)

    qc_path = _find_first(
        sample_dir,
        [
            f"{sample_id}.alignment_qc.json",
            f"{sample_id}.qc.json",
            "*alignment*qc*.json",
            "*.methyl_qc.json",
        ],
    )
    # Also accept QC under common export names
    if qc_path is None:
        for cand in sample_dir.glob("*.json"):
            data = _load_json(cand)
            if "guardrails" in data or "summary_stats" in data:
                qc_path = cand
                break
    require_aln_qc = thr.require_alignment_qc
    if require_aln_qc is None:
        require_aln_qc = True
    if require_aln_qc:
        report.checks.append(
            CheckResult("alignment_qc_present", qc_path is not None, str(qc_path))
        )
    elif qc_path is not None:
        report.artifacts["alignmentQcPath"] = str(qc_path)
    if qc_path is not None:
        qc = _load_json(qc_path)
        report.artifacts["alignmentQcPath"] = str(qc_path)
        report.metrics.update(_qc_metrics(qc))
        overall = report.metrics.get("overall_pass")
        if require_aln_qc:
            report.checks.append(
                CheckResult(
                    "alignment_qc_pass",
                    overall is True,
                    f"overall_pass={overall}",
                )
            )

    if mode == "pangenome_wgbs":
        gaf = _find_first(sample_dir, [f"{sample_id}.alignment.gaf", "*.gaf"])
        report.checks.append(CheckResult("gaf_present", gaf is not None, str(gaf)))
        if gaf:
            report.artifacts["gafPath"] = str(gaf)

    h5_files = _count_h5(sample_dir)
    report.artifacts["h5Files"] = h5_files
    report.metrics["n_h5_files"] = len(h5_files)
    report.checks.append(
        CheckResult("h5_outputs_present", len(h5_files) > 0, f"n={len(h5_files)}")
    )

    manifest = _extraction_manifest(sample_dir, sample_id)
    report.checks.append(
        CheckResult("extraction_manifest_present", manifest is not None, str(manifest))
    )
    if manifest is not None:
        man = _load_json(manifest)
        report.artifacts["extractionManifestPath"] = str(manifest)
        meta = man.get("metadata") if isinstance(man.get("metadata"), dict) else {}
        summary = man.get("summary") if isinstance(man.get("summary"), dict) else {}
        report.metrics["extraction_schema"] = meta.get("schema_name")
        report.metrics["cpg_weighted_mean_coverage"] = summary.get("cpg_weighted_mean_coverage")
        report.metrics["cpg_sites"] = (
            summary.get("cpg_sites")
            or summary.get("n_cpg_sites")
            or summary.get("cpg_sites_passing_min_cov")
        )
        if mode == "pangenome_wgbs":
            report.checks.append(
                CheckResult(
                    "canonical_extraction_manifest",
                    meta.get("schema_name") == "methylextractor.extraction_manifest",
                    f"schema_name={meta.get('schema_name')}",
                )
            )
            if "graph_assets" in man or "graph_assets" in meta:
                report.checks.append(CheckResult("graph_assets_fingerprint", True, "present"))
            else:
                report.checks.append(
                    CheckResult(
                        "graph_assets_fingerprint",
                        False,
                        "graph_assets missing on methylGrapher manifest",
                    )
                )

    eqc = _find_first(sample_dir, [f"{sample_id}.extraction_qc.json", "*extraction_qc*.json"])
    require_pass = thr.require_extraction_qc_pass
    if require_pass is None:
        require_pass = True
    if require_pass:
        report.checks.append(CheckResult("extraction_qc_present", eqc is not None, str(eqc)))
    if eqc is not None:
        eqc_data = _load_json(eqc)
        report.artifacts["extractionQcPath"] = str(eqc)
        guard = eqc_data.get("guardrails") if isinstance(eqc_data.get("guardrails"), dict) else {}
        passed = guard.get("overall_pass")
        report.metrics["extraction_qc_pass"] = passed
        if require_pass:
            report.checks.append(
                CheckResult("extraction_qc_pass", passed is True, f"overall_pass={passed}")
            )

    if mode == "pangenome_wgbs" and thr.require_read_level_patterns:
        patterns = sorted(sample_dir.glob("*.patterns.h5"))
        report.checks.append(
            CheckResult(
                "read_level_patterns",
                len(patterns) > 0,
                f"n_patterns={len(patterns)}",
            )
        )

    prep_log = sample_dir / f"{sample_id}.sample_prep_log.jsonl"
    report.checks.append(
        CheckResult("sample_prep_log_present", prep_log.is_file(), str(prep_log))
    )

    # Stock Giraffe: mark biological-parity checks as engineering-only success once
    # the workflow contract holds (BAM + QC + extract artifacts).
    if mode == "pangenome":
        report.checks.append(
            CheckResult(
                "stock_giraffe_engineering_comparator",
                True,
                "No methylation parity vs linear/methylGrapher required",
                engineering_only=True,
            )
        )

    # Recompute ok ignoring nothing — engineering_only still must be ok=True
    return report


def _load_cg_h5_sites(sample_dir: Path, chromosomes: Optional[Sequence[str]] = None) -> Dict[Tuple[str, int], Tuple[int, int]]:
    """Load ``*-CG.h5`` sites as (chrom, pos) → (mC, uC)."""
    try:
        import h5py  # type: ignore
        import numpy as np  # type: ignore
    except ImportError:
        return {}

    sites: Dict[Tuple[str, int], Tuple[int, int]] = {}
    paths = sorted(sample_dir.glob("*-CG.h5"))
    allow = {str(c).lstrip("chr") for c in chromosomes} if chromosomes else None
    for path in paths:
        chrom = path.name.split("-", 1)[0].lstrip("chr")
        if allow is not None and chrom not in allow:
            continue
        try:
            with h5py.File(path, "r") as handle:
                obj = handle["methylation_data"]
                if hasattr(obj, "dtype") and obj.dtype.names:
                    data = obj[:]
                    for row in data:
                        sites[(chrom, int(row["pos"]))] = (int(row["mC"]), int(row["uC"]))
                else:
                    pos = np.asarray(obj["pos"][:])
                    mc = np.asarray(obj["mC"][:])
                    uc = np.asarray(obj["uC"][:])
                    for p, m, u in zip(pos, mc, uc):
                        sites[(chrom, int(p))] = (int(m), int(u))
        except (OSError, KeyError, TypeError, ValueError):
            continue
    return sites


def compute_cg_overlap_stats(
    linear_dir: Path,
    wgbs_dir: Path,
    *,
    chromosomes: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Site-overlap / meth concordance stats between linear and wgbs CG H5 trees."""
    import math

    lin = _load_cg_h5_sites(linear_dir, chromosomes=chromosomes)
    wgbs = _load_cg_h5_sites(wgbs_dir, chromosomes=chromosomes)
    shared_keys = sorted(set(lin) & set(wgbs))
    ratios: List[float] = []
    abs_dmeth: List[float] = []
    lmeth: List[float] = []
    wmeth: List[float] = []
    for key in shared_keys:
        lm, lu = lin[key]
        wm, wu = wgbs[key]
        lcov = lm + lu
        wcov = wm + wu
        if lcov <= 0 or wcov <= 0:
            continue
        ratios.append(wcov / lcov)
        lm_f = lm / lcov
        wm_f = wm / wcov
        abs_dmeth.append(abs(wm_f - lm_f))
        lmeth.append(lm_f)
        wmeth.append(wm_f)

    pearson: Optional[float] = None
    if len(lmeth) >= 2:
        mean_l = sum(lmeth) / len(lmeth)
        mean_w = sum(wmeth) / len(wmeth)
        num = sum((a - mean_l) * (b - mean_w) for a, b in zip(lmeth, wmeth))
        den_l = math.sqrt(sum((a - mean_l) ** 2 for a in lmeth))
        den_w = math.sqrt(sum((b - mean_w) ** 2 for b in wmeth))
        if den_l > 0 and den_w > 0:
            pearson = num / (den_l * den_w)

    recall = (len(shared_keys) / len(lin)) if lin else None
    median_ratio = statistics.median(ratios) if ratios else None
    return {
        "linear_sites": len(lin),
        "wgbs_sites": len(wgbs),
        "shared_sites": len(shared_keys),
        "overlap_recall_of_linear": recall,
        "overlap_mean_abs_meth_delta": (
            sum(abs_dmeth) / len(abs_dmeth) if abs_dmeth else None
        ),
        "overlap_meth_pearson": pearson,
        "overlap_median_cov_ratio": median_ratio,
        "overlap_n_meth_pairs": len(abs_dmeth),
    }


def compare_linear_vs_wgbs(
    linear: ModeReport,
    wgbs: ModeReport,
    thresholds: Optional[SamplePrepCanaryThresholds] = None,
) -> List[CheckResult]:
    """Apply operator-set deltas between linear and pangenome_wgbs metrics."""
    thr = thresholds or SamplePrepCanaryThresholds()
    checks: List[CheckResult] = []

    def _num(rep: ModeReport, *keys: str) -> Optional[float]:
        for k in keys:
            v = rep.metrics.get(k)
            if v is None:
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        return None

    map_l = _num(linear, "mapping_rate", "percent_mapped")
    map_w = _num(wgbs, "mapping_rate", "percent_mapped")
    if thr.mapping_rate_delta is not None and map_l is not None and map_w is not None:
        # Normalize percent_mapped if needed
        if map_l > 1.0:
            map_l /= 100.0
        if map_w > 1.0:
            map_w /= 100.0
        delta = abs(map_l - map_w)
        checks.append(
            CheckResult(
                "mapping_rate_delta",
                delta <= float(thr.mapping_rate_delta),
                f"delta={delta:.4f} limit={thr.mapping_rate_delta}",
            )
        )

    dup_l = _num(linear, "duplication_rate", "percent_duplication")
    dup_w = _num(wgbs, "duplication_rate", "percent_duplication")
    if thr.duplication_rate_delta is not None and dup_l is not None and dup_w is not None:
        if dup_l > 1.0:
            dup_l /= 100.0
        if dup_w > 1.0:
            dup_w /= 100.0
        delta = abs(dup_l - dup_w)
        checks.append(
            CheckResult(
                "duplication_rate_delta",
                delta <= float(thr.duplication_rate_delta),
                f"delta={delta:.4f} limit={thr.duplication_rate_delta}",
            )
        )

    cov_l = _num(linear, "cpg_weighted_mean_coverage", "mean_coverage")
    cov_w = _num(wgbs, "cpg_weighted_mean_coverage", "mean_coverage")
    if thr.mean_coverage_delta is not None and cov_l is not None and cov_w is not None:
        delta = abs(cov_l - cov_w)
        checks.append(
            CheckResult(
                "mean_coverage_delta",
                delta <= float(thr.mean_coverage_delta),
                f"delta={delta:.4f} limit={thr.mean_coverage_delta}",
            )
        )

    sites_l = _num(linear, "cpg_sites")
    sites_w = _num(wgbs, "cpg_sites")
    # Prefer real site counts; never use n_h5_files as a science proxy.
    if (
        thr.cpg_sites_min_fraction_of_linear is not None
        and sites_l is not None
        and sites_w is not None
        and sites_l > 0
    ):
        frac = sites_w / sites_l
        checks.append(
            CheckResult(
                "cpg_sites_min_fraction_of_linear",
                frac >= float(thr.cpg_sites_min_fraction_of_linear),
                f"fraction={frac:.4f} min={thr.cpg_sites_min_fraction_of_linear}",
            )
        )

    if (
        thr.cpg_coverage_min_fraction_of_linear is not None
        and cov_l is not None
        and cov_w is not None
        and cov_l > 0
    ):
        frac = cov_w / cov_l
        checks.append(
            CheckResult(
                "cpg_coverage_min_fraction_of_linear",
                frac >= float(thr.cpg_coverage_min_fraction_of_linear),
                f"fraction={frac:.4f} min={thr.cpg_coverage_min_fraction_of_linear}",
            )
        )

    time_l = _num(linear, "alignment_duration_ms")
    time_w = _num(wgbs, "alignment_duration_ms")
    if (
        thr.alignment_time_ratio_max is not None
        and time_l is not None
        and time_w is not None
        and time_l > 0
    ):
        ratio = time_w / time_l
        checks.append(
            CheckResult(
                "alignment_time_ratio_max",
                ratio <= float(thr.alignment_time_ratio_max),
                f"ratio={ratio:.4f} max={thr.alignment_time_ratio_max} "
                f"linear_ms={time_l:.0f} wgbs_ms={time_w:.0f}",
            )
        )

    need_overlap = any(
        getattr(thr, name) is not None
        for name in (
            "overlap_recall_min_of_linear",
            "overlap_mean_abs_meth_delta_max",
            "overlap_meth_pearson_min",
            "overlap_median_cov_ratio_max",
        )
    )
    if need_overlap:
        stats = compute_cg_overlap_stats(
            linear.sample_dir,
            wgbs.sample_dir,
            chromosomes=thr.overlap_chromosomes,
        )
        linear.metrics.update({f"overlap_{k}": v for k, v in stats.items()})
        wgbs.metrics.update({f"overlap_{k}": v for k, v in stats.items()})
        recall = stats.get("overlap_recall_of_linear")
        if thr.overlap_recall_min_of_linear is not None and recall is not None:
            checks.append(
                CheckResult(
                    "overlap_recall_min_of_linear",
                    float(recall) >= float(thr.overlap_recall_min_of_linear),
                    f"recall={float(recall):.4f} min={thr.overlap_recall_min_of_linear} "
                    f"shared={stats.get('shared_sites')} linear={stats.get('linear_sites')}",
                )
            )
        dmeth = stats.get("overlap_mean_abs_meth_delta")
        if thr.overlap_mean_abs_meth_delta_max is not None and dmeth is not None:
            checks.append(
                CheckResult(
                    "overlap_mean_abs_meth_delta_max",
                    float(dmeth) <= float(thr.overlap_mean_abs_meth_delta_max),
                    f"mean_abs_dmeth={float(dmeth):.4f} max={thr.overlap_mean_abs_meth_delta_max}",
                )
            )
        pearson = stats.get("overlap_meth_pearson")
        if thr.overlap_meth_pearson_min is not None and pearson is not None:
            checks.append(
                CheckResult(
                    "overlap_meth_pearson_min",
                    float(pearson) >= float(thr.overlap_meth_pearson_min),
                    f"pearson={float(pearson):.4f} min={thr.overlap_meth_pearson_min}",
                )
            )
        cov_ratio = stats.get("overlap_median_cov_ratio")
        if thr.overlap_median_cov_ratio_max is not None and cov_ratio is not None:
            checks.append(
                CheckResult(
                    "overlap_median_cov_ratio_max",
                    float(cov_ratio) <= float(thr.overlap_median_cov_ratio_max),
                    f"median_ratio={float(cov_ratio):.4f} max={thr.overlap_median_cov_ratio_max}",
                )
            )
    return checks


def mode_report_to_dict(report: ModeReport) -> Dict[str, Any]:
    return {
        "mode": report.mode,
        "sample_id": report.sample_id,
        "sample_dir": str(report.sample_dir),
        "ok": all(c.ok for c in report.checks),
        "metrics": report.metrics,
        "artifacts": report.artifacts,
        "checks": [
            {
                "name": c.name,
                "ok": c.ok,
                "detail": c.detail,
                "engineering_only": c.engineering_only,
            }
            for c in report.checks
        ],
    }


def build_qualification_report(
    *,
    config: SamplePrepCanaryConfig,
    tier: str,
    mode_reports: Sequence[ModeReport],
    comparison_checks: Sequence[CheckResult],
    meta: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble machine-readable qualification JSON."""
    overall = all(all(c.ok for c in r.checks) for r in mode_reports) and all(
        c.ok for c in comparison_checks
    )
    return {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "tier": tier,
        "overall_pass": overall,
        "sample_id": config.sample_id,
        "source": config.source.model_dump(mode="python") if config.source else None,
        "modes": [mode_report_to_dict(r) for r in mode_reports],
        "linear_vs_wgbs": [
            {"name": c.name, "ok": c.ok, "detail": c.detail} for c in comparison_checks
        ],
        "thresholds": config.thresholds.model_dump(mode="python") if config.thresholds else None,
        "asset_pins": config.asset_pins.model_dump(mode="python") if config.asset_pins else None,
        "meta": dict(meta or {}),
        "policy": {
            "pangenome": "engineering_comparator_only",
            "pangenome_wgbs": "bisulfite_aware_parity_vs_linear",
            "linear": "biological_reference",
        },
    }


def write_junit(
    report: Mapping[str, Any],
    path: Path,
    *,
    suite_name: str = "sample_prep_canary",
) -> None:
    """Write a JUnit XML file from a qualification report."""
    testsuite = ET.Element("testsuite", name=suite_name)
    cases = 0
    failures = 0
    for mode in report.get("modes") or []:
        for check in mode.get("checks") or []:
            cases += 1
            tc = ET.SubElement(
                testsuite,
                "testcase",
                classname=f"{suite_name}.{mode.get('mode')}",
                name=str(check.get("name")),
            )
            if not check.get("ok"):
                failures += 1
                fail = ET.SubElement(tc, "failure", message=str(check.get("detail") or "failed"))
                fail.text = str(check.get("detail") or "")
    for check in report.get("linear_vs_wgbs") or []:
        cases += 1
        tc = ET.SubElement(
            testsuite,
            "testcase",
            classname=f"{suite_name}.linear_vs_wgbs",
            name=str(check.get("name")),
        )
        if not check.get("ok"):
            failures += 1
            fail = ET.SubElement(tc, "failure", message=str(check.get("detail") or "failed"))
            fail.text = str(check.get("detail") or "")
    testsuite.set("tests", str(cases))
    testsuite.set("failures", str(failures))
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(testsuite).write(path, encoding="utf-8", xml_declaration=True)


def write_markdown_summary(report: Mapping[str, Any], path: Path) -> None:
    """Write a concise Markdown qualification record."""
    lines = [
        "# SamplePrep real-data canary qualification",
        "",
        f"- Generated: `{report.get('generated_at_utc')}`",
        f"- Tier: `{report.get('tier')}`",
        f"- Overall pass: **{report.get('overall_pass')}**",
        f"- Sample id: `{report.get('sample_id')}`",
        "",
        "## Policy",
        "",
        "- `linear`: biological reference (Parabricks fq2bam_meth + MethylExtract)",
        "- `pangenome`: stock Giraffe engineering comparator (no methylation parity)",
        "- `pangenome_wgbs`: methylGrapher bisulfite-aware path vs linear thresholds",
        "",
        "## Modes",
        "",
    ]
    for mode in report.get("modes") or []:
        lines.append(f"### {mode.get('mode')} (`{mode.get('sample_id')}`)")
        lines.append("")
        lines.append(f"- ok: `{mode.get('ok')}`")
        lines.append(f"- sample_dir: `{mode.get('sample_dir')}`")
        metrics = mode.get("metrics") or {}
        if metrics:
            lines.append(f"- metrics: `{json.dumps(metrics, sort_keys=True)}`")
        lines.append("")
        lines.append("| Check | OK | Detail |")
        lines.append("|-------|----|--------|")
        for check in mode.get("checks") or []:
            lines.append(
                f"| {check.get('name')} | {check.get('ok')} | {check.get('detail')} |"
            )
        lines.append("")
    if report.get("linear_vs_wgbs"):
        lines.append("## Linear vs pangenome_wgbs")
        lines.append("")
        lines.append("| Check | OK | Detail |")
        lines.append("|-------|----|--------|")
        for check in report.get("linear_vs_wgbs") or []:
            lines.append(
                f"| {check.get('name')} | {check.get('ok')} | {check.get('detail')} |"
            )
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def resolve_canary_config(path: Optional[str | Path] = None) -> SamplePrepCanaryConfig:
    cfg = load_sample_prep_canary(path)
    if cfg is None:
        raise FileNotFoundError(
            "SamplePrep canary config not found; set METHYL_SAMPLE_PREP_CANARY_CONFIG "
            "or site testing.sample_prep_canary (see tests/real_data/sample_prep_canary/)"
        )
    return cfg
