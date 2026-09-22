"""Helpers to build domain objects from project config and worker outputs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .types import (
    AlignmentQcRef,
    ComparisonSpecRef,
    ExtractionQcRef,
    FragmentomicsRef,
    McIterationTaskConfig,
    MethylDetectionRef,
    MethylGroup,
    MethylSampleRef,
    MethylationMatrixRef,
    StratifiedCohortDraw,
    to_tagged_json,
)


def groups_from_project(
    project: Union[str, Path],
    *,
    role_by_label: Optional[Dict[str, str]] = None,
) -> List[MethylGroup]:
    """Build ``MethylGroup`` list from a project.json via methylutils."""
    from methyl_utils import load_project

    cfg = load_project(str(project))
    role_by_label = role_by_label or {}
    groups: List[MethylGroup] = []

    for label, paths, side in cfg._get_resolved_groups_with_side():
        role = role_by_label.get(label)
        if role is None:
            role = "control" if side == "control" else "disease"
        sample_refs = [
            MethylSampleRef(
                sampleId=Path(p).name,
                sampleDir=str(Path(p).resolve()),
            )
            for p in paths
        ]
        groups.append(
            MethylGroup(
                label=label,
                role=role,  # type: ignore[arg-type]
                sampleRefs=sample_refs,
                count=len(sample_refs),
                projectPath=str(Path(project).resolve() if Path(project).is_file() else project),
            )
        )
    return groups


def MethylGroup_from_project(project: Union[str, Path], group_label: str) -> MethylGroup:
    """Return a single ``MethylGroup`` by label."""
    for group in groups_from_project(project):
        if group.label == group_label:
            return group
    raise KeyError(f"group label not found in project: {group_label!r}")


def MethylDetectionRef_from_detector_output(
    output_json: Dict[str, Any],
    *,
    comparison_label: str,
    control_group: str,
    disease_group: str,
    output_dir: Optional[str] = None,
    chromosome: Optional[str] = None,
    context: Optional[str] = None,
) -> MethylDetectionRef:
    """Build ``MethylDetectionRef`` from worker ``output_json`` and known comparison metadata."""
    n_dmps = output_json.get("nDmps") or output_json.get("n_dmps")
    dmp_csv = output_json.get("dmpCsvPath") or output_json.get("dmp_csv_path")
    return MethylDetectionRef(
        comparisonLabel=comparison_label,
        controlGroup=control_group,
        diseaseGroup=disease_group,
        nDmps=int(n_dmps) if n_dmps is not None else None,
        dmpCsvPath=str(dmp_csv) if dmp_csv else None,
        outputDir=output_dir or output_json.get("outputDir"),
        chromosome=chromosome,
        context=context,
    )


def apply_qc_to_sample(
    sample: MethylSampleRef,
    *,
    qc_path: str,
    overall_pass: bool,
    guardrails: Optional[Dict[str, Any]] = None,
) -> MethylSampleRef:
    return sample.model_copy(
        update={
            "alignmentQc": AlignmentQcRef(
                qcPath=qc_path,
                overallPass=overall_pass,
                guardrails=guardrails,
            )
        }
    )


def apply_extraction_qc_to_sample(
    sample: MethylSampleRef,
    *,
    qc_path: str,
    overall_pass: bool,
    guardrails: Optional[Dict[str, Any]] = None,
) -> MethylSampleRef:
    return sample.model_copy(
        update={
            "extractionQc": ExtractionQcRef(
                qcPath=qc_path,
                overallPass=overall_pass,
                guardrails=guardrails,
            )
        }
    )


def apply_fragmentomics_to_sample(
    sample: MethylSampleRef,
    *,
    output_dir: str,
    summary_path: Optional[str] = None,
) -> MethylSampleRef:
    return sample.model_copy(
        update={
            "fragmentomics": FragmentomicsRef(
                outputDir=output_dir,
                summaryPath=summary_path,
            )
        }
    )


def apply_methylation_to_sample(
    sample: MethylSampleRef,
    *,
    chromosomes: List[str],
    contexts: List[str],
    h5_files: Optional[List[str]] = None,
) -> MethylSampleRef:
    return sample.model_copy(
        update={
            "methylation": MethylationMatrixRef(
                sampleDir=sample.sampleDir,
                chromosomes=chromosomes,
                contexts=contexts,
                h5Files=h5_files,
            )
        }
    )


def resolve_methylation_h5_path(
    sample: MethylSampleRef,
    chromosome: str,
    context: str = "CG",
) -> Path:
    """Resolve local HDF5 path from post-prep ``MethylSampleRef.methylation``."""
    if sample.methylation is None:
        raise ValueError(f"sample {sample.sampleId!r} has no methylation ref")
    matrix = sample.methylation
    if matrix.h5Files:
        suffix = f"{chromosome}-{context}.h5"
        for name in matrix.h5Files:
            if name == suffix or name.endswith(f"/{suffix}"):
                return Path(matrix.sampleDir) / Path(name).name
    pattern = matrix.h5Pattern or "{chr}-{ctx}.h5"
    filename = pattern.replace("{chr}", str(chromosome)).replace("{ctx}", str(context))
    return Path(matrix.sampleDir) / filename


def load_methyl_sample_from_ref(
    sample: MethylSampleRef,
    chromosome: str,
    context: str = "CG",
):
    """Load GPU-capable ``MethylSample`` from a post-prep sample ref."""
    from methyl_utils import MethylSample

    path = resolve_methylation_h5_path(sample, chromosome, context)
    if not path.is_file():
        raise FileNotFoundError(f"methylation HDF5 not found: {path}")
    return MethylSample.load_from_h5(path)


def build_stratified_cohort_draw(
    *,
    run_id: str,
    phase: str,
    project_path: str,
    groups: List[MethylGroup],
    comparisons: List[ComparisonSpecRef],
    seed: Optional[int] = None,
    train_fraction: Optional[float] = None,
    task_config: Optional[McIterationTaskConfig] = None,
) -> Dict[str, Any]:
    """Build tagged JSON dict for one MC iteration (for context_json.iterations[])."""
    draw = StratifiedCohortDraw(
        runId=run_id,
        phase=phase,
        projectPath=project_path,
        groups=groups,
        comparisons=comparisons,
        seed=seed,
        trainFraction=train_fraction,
        taskConfig=task_config,
    )
    return to_tagged_json(draw)


def _safe_cohort_filename_label(label: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", label.strip())
    return s or "cohort"


def _csv_sample_count(path: Path) -> int:
    if not path.is_file():
        return 0
    with open(path, encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip()]
    return max(0, len(lines) - 1)


def comparisons_from_project_json(project_path: Union[str, Path]) -> List[ComparisonSpecRef]:
    """Build ``ComparisonSpecRef`` list from a run or base ``project.json``."""
    with open(project_path, encoding="utf-8") as f:
        raw = json.load(f)
    out: List[ComparisonSpecRef] = []
    for item in raw.get("comparisons") or []:
        if not isinstance(item, dict):
            continue
        ctrl = item.get("control_group") or item.get("controlGroup")
        dis = item.get("disease_group") or item.get("diseaseGroup")
        if not ctrl or not dis:
            continue
        label = item.get("label") or item.get("comparison_label") or item.get("comparisonLabel")
        out.append(
            ComparisonSpecRef(
                controlGroup=str(ctrl),
                diseaseGroup=str(dis),
                comparisonLabel=str(label) if label else None,
            )
        )
    return out


def groups_from_mc_run_dir(
    run_dir: Union[str, Path],
    project_path: Union[str, Path],
    *,
    layout: str,
    cohort_labels: Optional[List[str]] = None,
) -> List[MethylGroup]:
    """Build ``MethylGroup`` train/val CSV handles for one Monte Carlo run directory."""
    run_dir = Path(run_dir)
    project_path = Path(project_path)
    project_str = str(project_path.resolve())
    groups: List[MethylGroup] = []

    if layout == "binary":
        with open(project_path, encoding="utf-8") as f:
            project = json.load(f)
        control_label = (
            (project.get("controls") or {}).get("label")
            or ((project.get("controls") or {}).get("groups") or [{}])[0].get("label")
            or "control"
        )
        disease_label = (
            (project.get("diseases") or {}).get("label")
            or ((project.get("diseases") or {}).get("groups") or [{}])[0].get("label")
            or "disease"
        )
        pairs = (
            (control_label, "control", run_dir / "train_control.csv", run_dir / "test_control.csv", run_dir / "val_control.csv"),
            (disease_label, "disease", run_dir / "train_disease.csv", run_dir / "test_disease.csv", run_dir / "val_disease.csv"),
        )
        for label, role, train_csv, test_csv, val_csv in pairs:
            resolved_test = test_csv if test_csv.is_file() else val_csv
            groups.append(
                MethylGroup(
                    label=str(label),
                    role=role,  # type: ignore[arg-type]
                    trainCsv=str(train_csv.resolve()) if train_csv.is_file() else None,
                    testCsv=str(resolved_test.resolve()) if resolved_test.is_file() else None,
                    valCsv=str(resolved_test.resolve()) if resolved_test.is_file() else None,
                    count=_csv_sample_count(train_csv) if train_csv.is_file() else None,
                    projectPath=project_str,
                )
            )
        return groups

    labels = list(cohort_labels or [])
    if not labels:
        labels = [
            p.name.removeprefix("training_").removesuffix(".csv")
            for p in sorted(run_dir.glob("training_*.csv"))
        ]
    for label in labels:
        safe = _safe_cohort_filename_label(label)
        train_csv = run_dir / f"training_{safe}.csv"
        test_csv = run_dir / f"test_{safe}.csv"
        val_csv = run_dir / f"testing_{safe}.csv"
        resolved_test = test_csv if test_csv.is_file() else val_csv
        groups.append(
            MethylGroup(
                label=label,
                role="other",
                trainCsv=str(train_csv.resolve()) if train_csv.is_file() else None,
                testCsv=str(resolved_test.resolve()) if resolved_test.is_file() else None,
                valCsv=str(resolved_test.resolve()) if resolved_test.is_file() else None,
                count=_csv_sample_count(train_csv) if train_csv.is_file() else None,
                projectPath=project_str,
            )
        )
    return groups


def enrich_sample_prep_output(
    action_name: str,
    sample: MethylSampleRef,
    output_json: Dict[str, Any],
    *,
    chromosomes: Optional[List[str]] = None,
    contexts: Optional[List[str]] = None,
) -> MethylSampleRef:
    """Optional worker adapter: merge ACTION output into ``MethylSampleRef``."""
    if action_name == "sample.download_fastq":
        files = output_json.get("fastqFiles")
        return sample.model_copy(update={"fastqFiles": files})
    if action_name in (
        "sample.parabricks_fq2bam",
        "sample.parabricks_giraffe",
        "sample.methylgrapher_wgbs_align",
    ):
        return sample.model_copy(
            update={
                "bamPath": output_json.get("bamPath"),
                "metricsJson": output_json.get("metricsJson"),
            }
        )
    if action_name == "sample.methyl_qc":
        guardrails = dict(output_json.get("guardrails") or {})
        screening = output_json.get("screening")
        if screening:
            guardrails.setdefault("screening", screening)
        qc_history = output_json.get("qcHistory")
        if qc_history:
            guardrails["qc_history"] = qc_history
        return apply_qc_to_sample(
            sample,
            qc_path=str(output_json.get("qcPath", "")),
            overall_pass=bool(guardrails.get("overall_pass", False)),
            guardrails=guardrails,
        )
    if action_name == "sample.extraction_qc":
        guardrails = dict(output_json.get("guardrails") or {})
        return apply_extraction_qc_to_sample(
            sample,
            qc_path=str(output_json.get("qcPath", "")),
            overall_pass=bool(guardrails.get("overall_pass", False)),
            guardrails=guardrails,
        )
    if action_name == "sample.fragmentomics":
        out_dir = str(output_json.get("outputDir", ""))
        summary_path = None
        if out_dir:
            candidate = Path(out_dir) / "sample_features.json"
            if candidate.is_file():
                summary_path = str(candidate)
        return apply_fragmentomics_to_sample(sample, output_dir=out_dir, summary_path=summary_path)
    if action_name in ("sample.methyl_extract", "sample.methylgrapher_wgbs_extract"):
        return apply_methylation_to_sample(
            sample,
            chromosomes=chromosomes or [],
            contexts=contexts or ["CG"],
            h5_files=output_json.get("h5Files"),
        )
    if action_name == "sample.archive_sample":
        archive = {
            "remotePrefix": output_json.get("remotePrefix"),
            "uploadedFiles": output_json.get("uploadedFiles") or [],
            "skippedFiles": output_json.get("skippedFiles") or [],
            "archiveMode": output_json.get("archiveMode"),
            "sampleArchived": output_json.get("sampleArchived"),
            "archiveSkipped": output_json.get("archiveSkipped"),
            "skipReason": output_json.get("skipReason"),
            "missingConfiguration": output_json.get("missingConfiguration") or [],
        }
        return sample.model_copy(update={"h5Archive": archive, "sampleArchive": archive})
    if action_name == "sample.qc_failed":
        return sample.model_copy(update={"status": "QC_FAILED"})
    return sample


def build_resolved_project(
    project: Union[str, Path],
    *,
    monte_carlo_runs_root: Optional[Union[str, Path]] = None,
    cohort_paths_list: Optional[List[tuple[str, List[str]]]] = None,
) -> "ResolvedProject":
    """
    Materialize study manifest paths and cohort membership for worker task inputs.

    Single seam that reads ``/work`` study manifests; downstream worker actions should
    bind concrete fields from the returned ``ResolvedProject`` instead of re-resolving
    sample paths from ``project.json``.
    """
    from methyl_utils import load_project

    from .types import ResolvedComparison, ResolvedProject

    project_path = Path(str(project)).expanduser().resolve()
    cfg = load_project(str(project_path))
    groups = groups_from_project(project_path)

    paths_by_label: Dict[str, List[str]] = {}
    for label, paths, _side in cfg._get_resolved_groups_with_side():
        paths_by_label[str(label)] = [str(Path(p).resolve()) for p in paths]

    comparisons: List[ResolvedComparison] = []
    for cmp in cfg.get_comparisons():
        control = str(cmp.control_group)
        disease = str(cmp.disease_group)
        label = cmp.comparison_label or disease or f"{control}_vs_{disease}"
        comparisons.append(
            ResolvedComparison(
                label=str(label),
                controlGroup=control,
                diseaseGroup=disease,
                comparisonLabel=str(label),
                centroid1Dir=str(cfg.get_centroid_dir("control", control)),
                centroid2Dir=str(cfg.get_centroid_dir("disease", disease)),
                detectOutDir=str(cfg.get_detection_output_dir(control, disease)),
                mapperOutDir=str(cfg.get_mapper_output_dir(control, disease)),
                enricherOutDir=str(cfg.get_enricher_output_dir(control, disease)),
                classifierOutDir=str(cfg.get_classifier_output_dir(control, disease)),
                controlSamplePaths=list(paths_by_label.get(control, [])),
                diseaseSamplePaths=list(paths_by_label.get(disease, [])),
            )
        )

    centroid1_dir: Optional[str] = None
    if comparisons:
        centroid1_dir = comparisons[0].centroid1Dir

    seed_groups = None
    if monte_carlo_runs_root and cohort_paths_list:
        from methyl_validation.project_gen import build_centroid_seed_groups

        seed_groups = build_centroid_seed_groups(
            monte_carlo_runs_root=monte_carlo_runs_root,
            base_project_path=project_path,
            cohort_paths_list=cohort_paths_list,
        )

    return ResolvedProject(
        projectPath=str(project_path),
        groups=groups,
        comparisons=comparisons,
        chromosomes=list(cfg.chromosomes or []),
        contexts=list(getattr(cfg, "contexts", None) or ["CG"]),
        centroid1Dir=centroid1_dir,
        centroidSeedGroups=seed_groups,
    )
