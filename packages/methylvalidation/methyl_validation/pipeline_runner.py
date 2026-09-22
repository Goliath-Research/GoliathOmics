"""
Orchestrate pipeline CLIs via subprocess.

Monte Carlo iterations run **methyl-centroid** and **methyl-detector**; when
resolved detection ``detection_mode`` is ``discovery_only``, **methyl-dmp-select**
runs next. With gene stability enabled, **methyl-mapper** and either **methyl-gene-select**
(split path) or in-process gene FeatureCuts (legacy) follow. **--freeze** runs
mapper/enricher; **--model** runs classifier then predictor.
"""

import shutil
import subprocess
import sys
import time
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Literal, Optional, Tuple
from methyl_utils.action_config_resolver import resolve_action_config_from_env, resolve_for_project

if TYPE_CHECKING:
    from .config import MonteCarloConfig


def _derive_previous_mc_run_dir(run_dir: Path) -> Optional[Path]:
    """Return monte_carlo_runs/run_{N-1} when run_dir is monte_carlo_runs/run_N."""
    name = run_dir.name
    if not name.startswith("run_"):
        return None
    try:
        run_number = int(name.split("_", 1)[1])
    except (IndexError, ValueError):
        return None
    if run_number <= 1:
        return None
    return run_dir.parent / f"run_{run_number - 1:04d}"


def _detection_config(project_json: str | Path) -> Dict[str, Any]:
    try:
        from methyl_utils import load_project

        project = load_project(project_json)
        return dict(resolve_for_project("detection", project))
    except Exception:
        try:
            with open(project_json, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                reg = data.get("regulatory")
                regulatory = dict(reg) if isinstance(reg, dict) else {}
                return dict(resolve_action_config_from_env("detection", regulatory=regulatory))
        except Exception:
            pass
        return {}


def _uses_discovery_only(project_json: str | Path) -> bool:
    return str(_detection_config(project_json).get("detection_mode") or "legacy") == "discovery_only"


def _dmp_select_group_labels(project_json: str | Path) -> List[Optional[str]]:
    """Return comparison labels for methyl-dmp-select (mirrors methyl-detector multi-comparison runs)."""
    try:
        from methyl_utils import load_project

        project = load_project(project_json)
        if project.uses_control_disease():
            comparisons = project.get_comparisons()
            if len(comparisons) > 1:
                return [spec.disease_group for spec in comparisons]
    except Exception:
        pass
    return [None]


def run_dmp_select(
    project_json: str | Path,
    detector_step_override: Optional[str | Path] = None,
) -> tuple[int, str, str]:
    """Run methyl-dmp-select for each comparison × chromosome after discovery-only detection."""
    try:
        from methyl_utils import load_project

        project = load_project(project_json)
        chromosomes = [str(c) for c in (project.chromosomes or ["1"])]
    except Exception as exc:
        return -1, "", f"methyl-dmp-select project load failed: {exc}"

    stdout_parts: List[str] = []
    stderr_parts: List[str] = []
    for group in _dmp_select_group_labels(project_json):
        for chrom in chromosomes:
            label = group or "default"
            cmd = ["methyl-dmp-select", "--project", str(project_json), "--chromosome", chrom]
            if group is not None:
                cmd.extend(["--group", str(group)])
            if detector_step_override is not None:
                cmd.extend(["--step-override", str(detector_step_override)])
            rc, out, err = run_cmd(cmd)
            stdout_parts.append(f"=== dmp-select {label} chr{chrom} stdout ===\n{out}")
            stderr_parts.append(f"=== dmp-select {label} chr{chrom} stderr ===\n{err}")
            if rc != 0:
                return rc, "\n".join(stdout_parts), "\n".join(stderr_parts)
    return 0, "\n".join(stdout_parts), "\n".join(stderr_parts)


def run_gene_select(
    project_json: str | Path,
    config: Optional["MonteCarloConfig"] = None,
    *,
    run_dir: Optional[Path] = None,
) -> tuple[int, str, str]:
    """Run methyl-gene-select for one MC iteration run directory."""
    project_json = Path(project_json).resolve()
    run_dir = Path(run_dir).resolve() if run_dir is not None else project_json.parent
    from methyl_gene_select.caps import resolve_gene_featurecuts_caps

    cmd = ["methyl-gene-select", "--project", str(project_json), "--run-dir", str(run_dir)]
    mc_caps: dict[str, object] = {}
    if config is not None:
        mc_caps = {
            "stability_gene_featurecuts_max_genes": getattr(
                config, "stability_gene_featurecuts_max_genes", None
            ),
            "stability_gene_featurecuts_max_dmps": getattr(
                config, "stability_gene_featurecuts_max_dmps", None
            ),
        }
    max_genes, max_dmps = resolve_gene_featurecuts_caps(
        resolved_config=mc_caps or None,
        run_dir=run_dir,
    )
    if max_genes is not None:
        cmd.extend(["--max-genes", str(max_genes)])
    if max_dmps is not None:
        cmd.extend(["--max-dmps", str(max_dmps)])
    if config is not None and bool(getattr(config, "stability_gene_biomarker_filter_enabled", False)):
        cmd.append("--biomarker-filter")
    return run_cmd(cmd)


def _append_gene_stability_steps(
    steps: List[Any],
    *,
    project_json: Path,
    per_cancer_group: bool,
    config: Optional["MonteCarloConfig"],
    split_detector: bool = False,
) -> None:
    if config is None or not bool(getattr(config, "stability_gene_featurecuts_enabled", False)):
        return
    steps.append(
        (
            "methyl-mapper",
            lambda: run_mapper(project_json, per_cancer_group=per_cancer_group, config=config),
            None,
            None,
        )
    )

    if split_detector:

        def _run_gene_select() -> tuple[int, str, str]:
            return run_gene_select(
                project_json,
                config,
                run_dir=Path(project_json).resolve().parent,
            )

        steps.append(
            (
                "methyl-gene-select",
                _run_gene_select,
                None,
                None,
            )
        )
        return

    def _run_gene_fc() -> tuple[int, str, str]:
        from .gene_featurecuts import run_gene_featurecuts_for_iteration

        return run_gene_featurecuts_for_iteration(project_json, config)

    steps.append(
        (
            "gene-featurecuts",
            _run_gene_fc,
            None,
            None,
        )
    )


def _format_duration(seconds: float) -> str:
    total = int(max(0, round(float(seconds))))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    if m > 0:
        return f"{m}m {s}s"
    return f"{s}s"


def _estimate_eta(completed_step_seconds: List[float], remaining_steps: int) -> str:
    if remaining_steps <= 0:
        return "0s"
    if not completed_step_seconds:
        return "unknown"
    avg = sum(completed_step_seconds) / max(1, len(completed_step_seconds))
    return _format_duration(avg * remaining_steps)


def _load_validation_metrics_from_output_dir(output_dir: Path) -> Optional[Dict[str, Any]]:
    """
    Load backend validation metrics from common output filenames.
    """
    candidates = (
        output_dir / "validation_metrics.json",
        output_dir / "metrics.json",
        output_dir / "training_metrics.json",
    )
    for p in candidates:
        if not p.is_file():
            continue
        try:
            with open(p, encoding="utf-8") as f:
                obj = json.load(f)
            if isinstance(obj, dict) and obj:
                return obj
        except Exception:
            continue
    return None


def _emit_clinical_performance_report(
    *,
    predictor_output_dir: Path,
    config: Optional["MonteCarloConfig"],
    source: str,
) -> None:
    if config is None:
        return
    metrics = _load_validation_metrics_from_output_dir(predictor_output_dir)
    if not metrics:
        return
    try:
        from .clinical_performance import write_clinical_performance_report

        write_clinical_performance_report(
            output_dir=predictor_output_dir,
            metrics=metrics,
            config=config,
            source=source,
        )
    except Exception:
        # Reporting should never break evaluation.
        return


def _find_cmd(name: str) -> Optional[str]:
    """Return path to CLI command if available."""
    return shutil.which(name)


def run_cmd(
    cmd: List[str],
    cwd: Optional[str | Path] = None,
    env: Optional[dict] = None,
) -> tuple[int, str, str]:
    """
    Run a command. Returns (returncode, stdout_str, stderr_str).
    """
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            env={**(env or {})} if env else None,
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stdout or "", result.stderr or ""
    except FileNotFoundError as e:
        return -1, "", str(e)
    except Exception as e:
        return -1, "", str(e)


def run_centroid(
    project_json: str | Path,
    centroid_step_overrides: Optional[Dict[str, str | Path]] = None,
) -> tuple[int, str, str]:
    """
    Run methyl-centroid for one or both cohorts.

    When group-specific step overrides are provided, runs group1 and group2 separately so
    each cohort can receive its own samples/add_samples/remove_samples delta payload.
    """
    if not centroid_step_overrides:
        cmd = ["methyl-centroid", "--project", str(project_json), "--group", "all"]
        return run_cmd(cmd)

    stdout_parts: List[str] = []
    stderr_parts: List[str] = []
    for group in ("group1", "group2"):
        override = centroid_step_overrides.get(group)
        cmd = ["methyl-centroid", "--project", str(project_json), "--group", group]
        if override is not None:
            cmd.extend(["--step-override", str(override)])
        rc, out, err = run_cmd(cmd)
        stdout_parts.append(f"=== {group} stdout ===\n{out}")
        stderr_parts.append(f"=== {group} stderr ===\n{err}")
        if rc != 0:
            return rc, "\n".join(stdout_parts), "\n".join(stderr_parts)

    return 0, "\n".join(stdout_parts), "\n".join(stderr_parts)


def run_centroid_group(
    project_json: str | Path,
    group: str,
    step_override: Optional[str | Path] = None,
) -> tuple[int, str, str]:
    """Run methyl-centroid for one specific group with optional step override."""
    cmd = ["methyl-centroid", "--project", str(project_json), "--group", str(group)]
    if step_override is not None:
        cmd.extend(["--step-override", str(step_override)])
    return run_cmd(cmd)


def _resolve_centroid_output_dir(
    project_json: str | Path,
    group: str,
    step_override: Optional[str | Path] = None,
) -> Optional[Path]:
    """
    Resolve centroid output directory for a project/group combination.

    Uses methyl-centroid's project resolver to match exactly where artifacts were written.
    """
    try:
        from methyl_centroid.project_resolver import resolve_centroid_batch_config

        batch = resolve_centroid_batch_config(project_json, group, step_override)
        output_dir = getattr(batch.base_config, "output_dir", None)
        if output_dir:
            return Path(output_dir)
    except Exception:
        return None
    return None


def _read_centroid_processed_samples(
    project_json: str | Path,
    group: str,
    step_override: Optional[str | Path] = None,
) -> Optional[int]:
    """
    Read actual processed sample count from centroid output metadata.

    Returns max(len(metadata.samples_used)) across produced centroid H5 files for the group.
    """
    output_dir = _resolve_centroid_output_dir(project_json, group, step_override)
    if output_dir is None or not output_dir.is_dir():
        return None
    try:
        from methyl_utils import MethylSample
    except Exception:
        return None

    samples_used_counts: List[int] = []
    for centroid_h5 in sorted(output_dir.glob("*.h5")):
        try:
            sample = MethylSample.load_from_h5(centroid_h5)
            metadata = getattr(sample, "metadata", None) or {}
            samples_used = metadata.get("samples_used")
            if isinstance(samples_used, list):
                samples_used_counts.append(len(samples_used))
        except Exception:
            continue

    if not samples_used_counts:
        return None
    return max(samples_used_counts)


def run_detector(
    project_json: str | Path,
    per_cancer_group: bool = False,
    detector_step_override: Optional[str | Path] = None,
) -> tuple[int, str, str]:
    """Run methyl-detector --project <project_json> [--per-cancer-group]. For binary single comparison, --per-cancer-group is optional."""
    cmd = ["methyl-detector", "--project", str(project_json)]
    if detector_step_override is not None:
        cmd.extend(["--step-override", str(detector_step_override)])
    if per_cancer_group:
        cmd.append("--per-cancer-group")
    return run_cmd(cmd)


def run_classifier(project_json: str | Path, per_cancer_group: bool = False) -> tuple[int, str, str]:
    """Run methyl-classifier --project <project_json> [--per-cancer-group]."""
    cmd = ["methyl-classifier", "--project", str(project_json)]
    if per_cancer_group:
        cmd.append("--per-cancer-group")
    return run_cmd(cmd)


def run_mapper(
    project_json: str | Path,
    per_cancer_group: bool = False,
    *,
    config: Optional["MonteCarloConfig"] = None,
) -> tuple[int, str, str]:
    """Run methyl-mapper --project <project_json>.

    Per-comparison layout is resolved automatically from the project; methyl-mapper
    does not accept --per-cancer-group (unlike methyl-detector/classifier).

    Mapper overrides come from ``resolvedConfig.mapper`` (profile/site) plus the
    MonteCarloConfig-derived CSV pattern for detector exports (discovery /
    selected / stable), not from sidecar JSON beside iteration ``project.json``.
    Pass ``config`` on local ``methyl-validation`` runs so the pattern matches
    detector outputs even when ``queue/mc_config.json`` was never written.
    """
    import tempfile

    del per_cancer_group  # kept for call-site compatibility
    cmd = ["methyl-mapper", "--project", str(project_json)]
    override = _mapper_override_dict(project_json, config=config)
    if override:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as handle:
            json.dump(override, handle)
            cmd.extend(["--step-override", handle.name])
    return run_cmd(cmd)


def _mapper_override_dict(
    project_json: str | Path,
    *,
    config: Optional["MonteCarloConfig"] = None,
) -> Dict[str, Any]:
    """Merge site/profile mapper config with MC-aware detector CSV pattern."""
    from methyl_utils.action_config_resolver import resolve_for_project
    from methyl_validation.mc_manifest import build_mapper_classifier_override

    merged: Dict[str, Any] = {}
    try:
        from methyl_utils import load_project

        project = load_project(project_json)
        merged = dict(resolve_for_project("mapper", project))
    except Exception:
        pass

    mc = config
    if mc is None:
        run_dir = Path(project_json).resolve().parent
        mc_root = run_dir.parent if run_dir.name.startswith("run_") else None
        mc_config_path = (mc_root / "queue" / "mc_config.json") if mc_root else None
        if mc_config_path is not None and mc_config_path.is_file():
            try:
                from methyl_validation.config import MonteCarloConfig

                mc = MonteCarloConfig.model_validate(
                    json.loads(mc_config_path.read_text(encoding="utf-8"))
                )
            except Exception:
                mc = None

    if mc is not None:
        # Prefer MC modeling / gene-FC loci source over stale site defaults so
        # mapper consumes the same detector export family the run requested.
        if bool(getattr(mc, "stability_gene_featurecuts_enabled", False)) or not (
            merged.get("csv_pattern") or merged.get("csv_filename_pattern")
        ):
            merged.update(build_mapper_classifier_override(mc))
    return merged


def _enricher_config(project_json: str | Path) -> Dict[str, Any]:
    try:
        from methyl_utils import load_project

        project = load_project(project_json)
        return dict(resolve_for_project("enricher", project))
    except Exception:
        try:
            with open(project_json, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                reg = data.get("regulatory")
                regulatory = dict(reg) if isinstance(reg, dict) else {}
                return dict(resolve_action_config_from_env("enricher", regulatory=regulatory))
        except Exception:
            pass
        return {}


def run_enricher(
    project_json: str | Path,
    per_cancer_group: bool = False,
    *,
    ensure_complete: Optional[bool] = None,
) -> tuple[int, str, str]:
    """Run methyl-enricher --project <project_json> with optional --ensure-complete."""
    cfg = _enricher_config(project_json)
    use_ec = bool(ensure_complete) if ensure_complete is not None else bool(cfg.get("ensure_complete", True))
    cmd = ["methyl-enricher", "--project", str(project_json)]
    if use_ec:
        cmd.append("--ensure-complete")
    if cfg.get("modules"):
        cmd.append("--modules")
    if per_cancer_group:
        pass  # per-comparison layout is automatic via --project
    return run_cmd(cmd)


def run_enricher_plan_tasks(project_json: str | Path) -> tuple[int, str, str]:
    """Plan distributed enricher tasks (does not call Enrichr)."""
    return run_cmd(["methyl-enricher", "plan-tasks", "--project", str(project_json)])


def run_enricher_verify_complete(project_json: str | Path) -> tuple[int, str, str]:
    """Verify all enricher comparisons have complete library outputs."""
    return run_cmd(["methyl-enricher", "verify-complete", "--project", str(project_json)])


def _progression_settings(project_json: str | Path) -> Dict[str, Any]:
    """Read progression step settings from project.json."""
    try:
        from methyl_utils import load_project
    except Exception:
        return {}
    try:
        project = load_project(project_json)
        return resolve_for_project("progression", project)
    except Exception:
        return {}


def run_progression(project_json: str | Path) -> tuple[int, str, str]:
    """Run methyl-disease-progression --project <project_json> with optional progression args."""
    cfg = _progression_settings(project_json)
    cmd = ["methyl-disease-progression", "--project", str(project_json)]
    out_dir = cfg.get("output_dir")
    if out_dir:
        cmd.extend(["--output-dir", str(out_dir)])
    ordered = cfg.get("ordered_comparison_labels") or cfg.get("ordered_disease_groups")
    if isinstance(ordered, list) and ordered:
        cmd.extend(["--ordered-comparison-labels", ",".join(str(x) for x in ordered)])
    if bool(cfg.get("strict_missing", False)):
        cmd.append("--strict-missing")
    if bool(cfg.get("report_md", False)):
        cmd.append("--report-md")
    return run_cmd(cmd)


def run_predictor(
    project_json: str | Path,
    test_control_csv: str | Path,
    test_disease_csv: str | Path,
    output_dir: str | Path,
) -> tuple[int, str, str]:
    """Run methyl-predictor with project and override test sets and output dir."""
    cmd = [
        "methyl-predictor",
        "--project", str(project_json),
        "--test-control", str(test_control_csv),
        "--test-disease", str(test_disease_csv),
        "--output-dir", str(output_dir),
    ]
    return run_cmd(cmd)


def run_predictor_multiclass(
    project_json: str | Path,
    test_groups_json: str | Path,
    output_dir: str | Path,
) -> tuple[int, str, str]:
    """Run methyl-predictor for flat multiclass: ``--test-groups`` JSON (list of {label, paths})."""
    cmd = [
        "methyl-predictor",
        "--project", str(project_json),
        "--test-groups", str(test_groups_json),
        "--output-dir", str(output_dir),
    ]
    return run_cmd(cmd)


def _write_step_log(log_path: Path, stdout: str, stderr: str) -> None:
    """Write combined stdout and stderr to a single log file with delimiters."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("=== stdout ===\n")
        f.write(stdout)
        if stdout and not stdout.endswith("\n"):
            f.write("\n")
        f.write("\n=== stderr ===\n")
        f.write(stderr)
        if stderr and not stderr.endswith("\n"):
            f.write("\n")


def run_pipeline_for_iteration(
    project_json: Path,
    per_cancer_group: bool = False,
    logs_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[int, str, Literal["start", "end"]], None]] = None,
    centroid_step_overrides: Optional[Dict[str, Path]] = None,
    detector_step_override: Optional[Path] = None,
    skip_centroid: bool = False,
    config: Optional["MonteCarloConfig"] = None,  # reserved; mapper/enricher belong to --freeze, not MC
    previous_run_dir: Optional[Path] = None,
) -> tuple[bool, List[str], List[Dict[str, Any]]]:
    """
    Monte Carlo stability iteration: methyl-centroid → methyl-detector
    (+ methyl-dmp-select when ``detection_mode=discovery_only``).

    Omits methyl-classifier and methyl-predictor (final model is ``--model`` after freeze).
    """
    from .validator_metrics import write_step_timings_csv
    from .project_gen import prepare_incremental_centroid_baseline

    errors: List[str] = []
    step_timings: List[Dict[str, Any]] = []
    steps: List[Tuple[str, Callable[[], tuple[int, str, str]], Optional[str], Optional[str | Path]]] = []
    run_dir = Path(project_json).resolve().parent
    if not skip_centroid and centroid_step_overrides:
        baseline_source = previous_run_dir
        if baseline_source is None:
            baseline_source = _derive_previous_mc_run_dir(run_dir)
        prepare_incremental_centroid_baseline(
            baseline_source,
            run_dir,
            centroid_step_overrides.get("group1"),
            centroid_step_overrides.get("group2"),
        )
    if not skip_centroid:
        if centroid_step_overrides:
            steps.extend(
                [
                    (
                        "methyl-centroid-group1",
                        lambda: run_centroid_group(
                            project_json,
                            "group1",
                            step_override=centroid_step_overrides.get("group1"),
                        ),
                        "group1",
                        centroid_step_overrides.get("group1"),
                    ),
                    (
                        "methyl-centroid-group2",
                        lambda: run_centroid_group(
                            project_json,
                            "group2",
                            step_override=centroid_step_overrides.get("group2"),
                        ),
                        "group2",
                        centroid_step_overrides.get("group2"),
                    ),
                ]
            )
        else:
            steps.append(
                (
                    "methyl-centroid",
                    lambda: run_centroid(project_json, centroid_step_overrides=centroid_step_overrides),
                    None,
                    None,
                )
            )
    steps.append(
        (
            "methyl-detector",
            lambda: run_detector(
                project_json,
                per_cancer_group=per_cancer_group,
                detector_step_override=detector_step_override,
            ),
            None,
            None,
        )
    )
    split_detector = _uses_discovery_only(project_json)
    if split_detector:
        steps.append(
            (
                "methyl-dmp-select",
                lambda: run_dmp_select(
                    project_json,
                    detector_step_override=detector_step_override,
                ),
                None,
                None,
            )
        )
    _append_gene_stability_steps(
        steps,
        project_json=project_json,
        per_cancer_group=per_cancer_group,
        config=config,
        split_detector=split_detector,
    )
    completed_seconds: List[float] = []
    total_steps = len(steps)
    for step_index, (step_name, run_fn, centroid_group, centroid_override) in enumerate(steps):
        if progress_callback is None:
            print(
                f"[mc] [{step_index + 1}/{total_steps}] running {step_name}...",
                file=sys.stderr,
                flush=True,
            )
        if progress_callback is not None:
            progress_callback(step_index, step_name, "start")
        t0 = time.perf_counter()
        rc, out, err = run_fn()
        duration_seconds = time.perf_counter() - t0
        if progress_callback is not None:
            progress_callback(step_index, step_name, "end")
        row: Dict[str, Any] = {
            "step_name": step_name,
            "duration_seconds": round(duration_seconds, 6),
            "return_code": rc,
        }
        if centroid_group is not None:
            n_processed = _read_centroid_processed_samples(
                project_json,
                centroid_group,
                step_override=centroid_override,
            )
            if n_processed is not None:
                row["n_processed_samples"] = int(n_processed)
        step_timings.append(row)
        if logs_dir is not None:
            log_path = logs_dir / f"{step_name}.log"
            _write_step_log(log_path, out, err)
        completed_seconds.append(duration_seconds)
        if progress_callback is None:
            remaining = total_steps - (step_index + 1)
            eta = _estimate_eta(completed_seconds, remaining)
            print(
                f"[mc] [{step_index + 1}/{total_steps}] {step_name} finished in "
                f"{_format_duration(duration_seconds)} (ETA {eta})",
                file=sys.stderr,
                flush=True,
            )
        if rc != 0:
            msg = f"{step_name} failed (exit {rc}). stderr: {err[:500] if err else 'none'}"
            errors.append(msg)
            if logs_dir is not None:
                write_step_timings_csv(step_timings, logs_dir.parent / "step_timings.csv")
            return False, errors, step_timings
    if logs_dir is not None:
        write_step_timings_csv(step_timings, logs_dir.parent / "step_timings.csv")
    return True, [], step_timings


def run_predictor_only_binary(
    project_json: Path,
    val_control_csv: Path,
    val_disease_csv: Path,
    predictor_output_dir: Path,
    logs_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[int, str, Literal["start", "end"]], None]] = None,
) -> tuple[bool, List[str], List[Dict[str, Any]]]:
    """Only methyl-predictor (frozen model paths must already be wired in project.json)."""
    from .validator_metrics import write_step_timings_csv

    errors: List[str] = []
    step_timings: List[Dict[str, Any]] = []
    steps = [
        (
            "methyl-predictor",
            lambda: run_predictor(
                project_json,
                val_control_csv,
                val_disease_csv,
                predictor_output_dir,
            ),
        ),
    ]
    completed_seconds: List[float] = []
    total_steps = len(steps)
    for step_index, (step_name, run_fn) in enumerate(steps):
        if progress_callback is None:
            print(
                f"[mc-predictor-only] [{step_index + 1}/{total_steps}] running {step_name}...",
                file=sys.stderr,
                flush=True,
            )
        if progress_callback is not None:
            progress_callback(step_index, step_name, "start")
        t0 = time.perf_counter()
        rc, out, err = run_fn()
        duration_seconds = time.perf_counter() - t0
        if progress_callback is not None:
            progress_callback(step_index, step_name, "end")
        step_timings.append({
            "step_name": step_name,
            "duration_seconds": round(duration_seconds, 6),
            "return_code": rc,
        })
        if logs_dir is not None:
            log_path = logs_dir / f"{step_name}.log"
            _write_step_log(log_path, out, err)
        completed_seconds.append(duration_seconds)
        if progress_callback is None:
            remaining = total_steps - (step_index + 1)
            eta = _estimate_eta(completed_seconds, remaining)
            print(
                f"[mc-predictor-only] [{step_index + 1}/{total_steps}] {step_name} finished in "
                f"{_format_duration(duration_seconds)} (ETA {eta})",
                file=sys.stderr,
                flush=True,
            )
        if rc != 0:
            msg = f"{step_name} failed (exit {rc}). stderr: {err[:500] if err else 'none'}"
            errors.append(msg)
            if logs_dir is not None:
                write_step_timings_csv(step_timings, logs_dir.parent / "step_timings.csv")
            return False, errors, step_timings
    if logs_dir is not None:
        write_step_timings_csv(step_timings, logs_dir.parent / "step_timings.csv")
    return True, [], step_timings


def run_predictor_only_multiclass(
    project_json: Path,
    test_groups_json: Path,
    predictor_output_dir: Path,
    logs_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[int, str, Literal["start", "end"]], None]] = None,
) -> tuple[bool, List[str], List[Dict[str, Any]]]:
    """Only methyl-predictor for multiclass (frozen model paths in project.json)."""
    from .validator_metrics import write_step_timings_csv

    errors: List[str] = []
    step_timings: List[Dict[str, Any]] = []
    steps = [
        (
            "methyl-predictor",
            lambda: run_predictor_multiclass(
                project_json,
                test_groups_json,
                predictor_output_dir,
            ),
        ),
    ]
    completed_seconds: List[float] = []
    total_steps = len(steps)
    for step_index, (step_name, run_fn) in enumerate(steps):
        if progress_callback is None:
            print(
                f"[mc-predictor-only] [{step_index + 1}/{total_steps}] running {step_name}...",
                file=sys.stderr,
                flush=True,
            )
        if progress_callback is not None:
            progress_callback(step_index, step_name, "start")
        t0 = time.perf_counter()
        rc, out, err = run_fn()
        duration_seconds = time.perf_counter() - t0
        if progress_callback is not None:
            progress_callback(step_index, step_name, "end")
        step_timings.append({
            "step_name": step_name,
            "duration_seconds": round(duration_seconds, 6),
            "return_code": rc,
        })
        if logs_dir is not None:
            log_path = logs_dir / f"{step_name}.log"
            _write_step_log(log_path, out, err)
        completed_seconds.append(duration_seconds)
        if progress_callback is None:
            remaining = total_steps - (step_index + 1)
            eta = _estimate_eta(completed_seconds, remaining)
            print(
                f"[mc-predictor-only] [{step_index + 1}/{total_steps}] {step_name} finished in "
                f"{_format_duration(duration_seconds)} (ETA {eta})",
                file=sys.stderr,
                flush=True,
            )
        if rc != 0:
            msg = f"{step_name} failed (exit {rc}). stderr: {err[:500] if err else 'none'}"
            errors.append(msg)
            if logs_dir is not None:
                write_step_timings_csv(step_timings, logs_dir.parent / "step_timings.csv")
            return False, errors, step_timings
    if logs_dir is not None:
        write_step_timings_csv(step_timings, logs_dir.parent / "step_timings.csv")
    return True, [], step_timings


def run_post_model_validation_binary(
    project_json: Path,
    val_control_csv: Path,
    val_disease_csv: Path,
    predictor_output_dir: Path,
    production_output_dir: Path,
    logs_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[int, str, Literal["start", "end"]], None]] = None,
    config: Optional["MonteCarloConfig"] = None,
) -> tuple[bool, List[str], List[Dict[str, Any]]]:
    """Evaluate frozen production model on one binary MC holdout split."""
    from .validator_metrics import write_step_timings_csv

    backend = (config.model_backend if config is not None else "ecdf").strip().lower()
    errors: List[str] = []
    step_timings: List[Dict[str, Any]] = []
    predictor_output_dir.mkdir(parents=True, exist_ok=True)
    model_dir = production_output_dir / "classifiers"

    if backend == "tabular_sklearn":
        def _run_eval() -> tuple[int, str, str]:
            try:
                from .tabular_backend import predict_tabular_model_from_project

                metrics = predict_tabular_model_from_project(
                    project_json=project_json,
                    model_dir=model_dir,
                    output_dir=predictor_output_dir,
                    covariates_path=(config.covariates_path if config is not None else None),
                    covariate_id_column=(config.covariate_id_column if config is not None else "sample_id"),
                    covariates_strict_join=(config.covariates_strict_join if config is not None else False),
                    covariates_missing_samples=(
                        config.covariates_missing_samples if config is not None else None
                    ),
                    observed_feature_min_obs_fraction=(
                        config.observed_feature_min_obs_fraction if config is not None else 0.0
                    ),
                    evaluation_partition="test",
                )
                return 0, json.dumps(metrics), ""
            except Exception as e:
                return 1, "", str(e)
        steps = [("tabular-predictor", _run_eval)]
    elif backend == "generative_hybrid":
        def _run_eval() -> tuple[int, str, str]:
            try:
                from .generative_backend import predict_generative_model_from_project

                metrics = predict_generative_model_from_project(
                    project_json=project_json,
                    model_dir=model_dir,
                    output_dir=predictor_output_dir,
                    covariates_path=(config.covariates_path if config is not None else None),
                    covariate_id_column=(config.covariate_id_column if config is not None else "sample_id"),
                    covariates_strict_join=(config.generative_covariates_strict if config is not None else True),
                    covariates_missing_samples=(
                        config.covariates_missing_samples if config is not None else None
                    ),
                    observed_feature_min_obs_fraction=(
                        config.observed_feature_min_obs_fraction if config is not None else 0.0
                    ),
                    evaluation_partition="test",
                )
                return 0, json.dumps(metrics), ""
            except Exception as e:
                return 1, "", str(e)
        steps = [("generative-predictor", _run_eval)]
    else:
        steps = [
            (
                "methyl-predictor",
                lambda: run_predictor(
                    project_json,
                    val_control_csv,
                    val_disease_csv,
                    predictor_output_dir,
                ),
            ),
        ]

    completed_seconds: List[float] = []
    total_steps = len(steps)
    for step_index, (step_name, run_fn) in enumerate(steps):
        if progress_callback is None:
            print(
                f"[post-model-validation] [{step_index + 1}/{total_steps}] running {step_name}...",
                file=sys.stderr,
                flush=True,
            )
        if progress_callback is not None:
            progress_callback(step_index, step_name, "start")
        t0 = time.perf_counter()
        rc, out, err = run_fn()
        duration_seconds = time.perf_counter() - t0
        if progress_callback is not None:
            progress_callback(step_index, step_name, "end")
        step_timings.append({
            "step_name": step_name,
            "duration_seconds": round(duration_seconds, 6),
            "return_code": rc,
        })
        if logs_dir is not None:
            _write_step_log(logs_dir / f"{step_name}.log", out, err)
        completed_seconds.append(duration_seconds)
        if progress_callback is None:
            remaining = total_steps - (step_index + 1)
            eta = _estimate_eta(completed_seconds, remaining)
            print(
                f"[post-model-validation] [{step_index + 1}/{total_steps}] {step_name} finished in "
                f"{_format_duration(duration_seconds)} (ETA {eta})",
                file=sys.stderr,
                flush=True,
            )
        if rc != 0:
            errors.append(f"{step_name} failed (exit {rc}). stderr: {err[:500] if err else 'none'}")
            if logs_dir is not None:
                write_step_timings_csv(step_timings, logs_dir.parent / "step_timings.csv")
            return False, errors, step_timings
    if logs_dir is not None:
        write_step_timings_csv(step_timings, logs_dir.parent / "step_timings.csv")
    _emit_clinical_performance_report(
        predictor_output_dir=predictor_output_dir,
        config=config,
        source="post_model_validation_binary",
    )
    return True, [], step_timings


def run_post_model_validation_multiclass(
    project_json: Path,
    test_groups_json: Path,
    predictor_output_dir: Path,
    production_output_dir: Path,
    logs_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[int, str, Literal["start", "end"]], None]] = None,
    config: Optional["MonteCarloConfig"] = None,
) -> tuple[bool, List[str], List[Dict[str, Any]]]:
    """Evaluate frozen production model on one multiclass/hierarchical MC holdout split."""
    from .validator_metrics import write_step_timings_csv

    backend = (config.model_backend if config is not None else "ecdf").strip().lower()
    errors: List[str] = []
    step_timings: List[Dict[str, Any]] = []
    predictor_output_dir.mkdir(parents=True, exist_ok=True)
    model_dir = production_output_dir / "classifiers"

    if backend == "tabular_sklearn":
        def _run_eval() -> tuple[int, str, str]:
            try:
                from .tabular_backend import predict_tabular_model_from_project

                metrics = predict_tabular_model_from_project(
                    project_json=project_json,
                    model_dir=model_dir,
                    output_dir=predictor_output_dir,
                    covariates_path=(config.covariates_path if config is not None else None),
                    covariate_id_column=(config.covariate_id_column if config is not None else "sample_id"),
                    covariates_strict_join=(config.covariates_strict_join if config is not None else False),
                    covariates_missing_samples=(
                        config.covariates_missing_samples if config is not None else None
                    ),
                    observed_feature_min_obs_fraction=(
                        config.observed_feature_min_obs_fraction if config is not None else 0.0
                    ),
                    evaluation_partition="test",
                )
                return 0, json.dumps(metrics), ""
            except Exception as e:
                return 1, "", str(e)
        steps = [("tabular-predictor", _run_eval)]
    elif backend == "generative_hybrid":
        def _run_eval() -> tuple[int, str, str]:
            try:
                from .generative_backend import predict_generative_model_from_project

                metrics = predict_generative_model_from_project(
                    project_json=project_json,
                    model_dir=model_dir,
                    output_dir=predictor_output_dir,
                    covariates_path=(config.covariates_path if config is not None else None),
                    covariate_id_column=(config.covariate_id_column if config is not None else "sample_id"),
                    covariates_strict_join=(config.generative_covariates_strict if config is not None else True),
                    covariates_missing_samples=(
                        config.covariates_missing_samples if config is not None else None
                    ),
                    observed_feature_min_obs_fraction=(
                        config.observed_feature_min_obs_fraction if config is not None else 0.0
                    ),
                    evaluation_partition="test",
                )
                return 0, json.dumps(metrics), ""
            except Exception as e:
                return 1, "", str(e)
        steps = [("generative-predictor", _run_eval)]
    else:
        steps = [
            (
                "methyl-predictor",
                lambda: run_predictor_multiclass(
                    project_json,
                    test_groups_json,
                    predictor_output_dir,
                ),
            ),
        ]

    completed_seconds: List[float] = []
    total_steps = len(steps)
    for step_index, (step_name, run_fn) in enumerate(steps):
        if progress_callback is None:
            print(
                f"[post-model-validation] [{step_index + 1}/{total_steps}] running {step_name}...",
                file=sys.stderr,
                flush=True,
            )
        if progress_callback is not None:
            progress_callback(step_index, step_name, "start")
        t0 = time.perf_counter()
        rc, out, err = run_fn()
        duration_seconds = time.perf_counter() - t0
        if progress_callback is not None:
            progress_callback(step_index, step_name, "end")
        step_timings.append({
            "step_name": step_name,
            "duration_seconds": round(duration_seconds, 6),
            "return_code": rc,
        })
        if logs_dir is not None:
            _write_step_log(logs_dir / f"{step_name}.log", out, err)
        completed_seconds.append(duration_seconds)
        if progress_callback is None:
            remaining = total_steps - (step_index + 1)
            eta = _estimate_eta(completed_seconds, remaining)
            print(
                f"[post-model-validation] [{step_index + 1}/{total_steps}] {step_name} finished in "
                f"{_format_duration(duration_seconds)} (ETA {eta})",
                file=sys.stderr,
                flush=True,
            )
        if rc != 0:
            errors.append(f"{step_name} failed (exit {rc}). stderr: {err[:500] if err else 'none'}")
            if logs_dir is not None:
                write_step_timings_csv(step_timings, logs_dir.parent / "step_timings.csv")
            return False, errors, step_timings
    if logs_dir is not None:
        write_step_timings_csv(step_timings, logs_dir.parent / "step_timings.csv")
    _emit_clinical_performance_report(
        predictor_output_dir=predictor_output_dir,
        config=config,
        source="post_model_validation_multiclass",
    )
    return True, [], step_timings


def run_pipeline_for_production(
    project_json: Path,
    logs_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[int, str, Literal["start", "end"]], None]] = None,
    skip_centroid: bool = False,
    skip_detection: bool = False,
    config: Optional["MonteCarloConfig"] = None,
) -> Tuple[bool, List[str], List[Dict[str, Any]]]:
    """
    Production freeze build: centroid -> detector (fixed_dmp_panel) -> mapper -> enricher.
    Does not run methyl-classifier or methyl-predictor; use --model to run classifier and predictor sequentially.
    Fragmentomics (methyl-fragmentomics) runs only in SamplePrepPipeline after alignment and before extraction.
    """
    from .validator_metrics import write_step_timings_csv

    errors: List[str] = []
    step_timings: List[Dict[str, Any]] = []
    steps: List[Tuple[str, Callable[[], tuple[int, str, str]]]] = []
    # Detector reuse implies centroid reuse as well for freeze runs.
    effective_skip_centroid = bool(skip_centroid or skip_detection)
    if not effective_skip_centroid:
        steps.append(("methyl-centroid", lambda: run_centroid(project_json, centroid_step_overrides=None)))
    if not skip_detection:
        steps.append(
            ("methyl-detector", lambda: run_detector(project_json, per_cancer_group=False))
        )
        if _uses_discovery_only(project_json) and not _detection_config(project_json).get(
            "fixed_dmp_panel"
        ):
            steps.append(
                (
                    "methyl-dmp-select",
                    lambda: run_dmp_select(project_json),
                )
            )
    steps.append(
        ("methyl-mapper", lambda: run_mapper(project_json, per_cancer_group=False))
    )
    enricher_cfg = _enricher_config(project_json)
    skip_enricher = bool(enricher_cfg.get("skip"))
    progression_cfg = _progression_settings(project_json)
    progression_enabled = bool(progression_cfg.get("enabled", False))
    enricher_distributed = bool(enricher_cfg.get("distributed", False))
    if not skip_enricher:
        if enricher_distributed:
            steps.append(
                (
                    "methyl-enricher-plan-tasks",
                    lambda: run_enricher_plan_tasks(project_json),
                ),
            )
            print(
                "[freeze] enricher.distributed=true: planned queue tasks only. "
                "Run workers (methyl-enricher run-task) then verify-complete before progression.",
                file=sys.stderr,
                flush=True,
            )
        else:
            steps.append(
                ("methyl-enricher", lambda: run_enricher(project_json, per_cancer_group=False)),
            )
            if progression_enabled:
                steps.append(
                    ("methyl-disease-progression", lambda: run_progression(project_json)),
                )

    completed_seconds: List[float] = []
    total_steps = len(steps)
    for step_index, (step_name, run_fn) in enumerate(steps):
        print(
            f"[freeze] [{step_index + 1}/{total_steps}] running {step_name}...",
            file=sys.stderr,
            flush=True,
        )
        if progress_callback is not None:
            progress_callback(step_index, step_name, "start")
        t0 = time.perf_counter()
        rc, out, err = run_fn()
        duration_seconds = time.perf_counter() - t0
        if progress_callback is not None:
            progress_callback(step_index, step_name, "end")
        step_timings.append({
            "step_name": step_name,
            "duration_seconds": round(duration_seconds, 6),
            "return_code": rc,
        })
        if logs_dir is not None:
            log_path = logs_dir / f"{step_name}.log"
            _write_step_log(log_path, out, err)
        completed_seconds.append(duration_seconds)
        remaining = total_steps - (step_index + 1)
        eta = _estimate_eta(completed_seconds, remaining)
        print(
            f"[freeze] [{step_index + 1}/{total_steps}] {step_name} finished in "
            f"{_format_duration(duration_seconds)} (ETA {eta})",
            file=sys.stderr,
            flush=True,
        )
        if rc != 0:
            msg = f"{step_name} failed (exit {rc}). stderr: {err[:500] if err else 'none'}"
            errors.append(msg)
            if logs_dir is not None:
                write_step_timings_csv(step_timings, logs_dir.parent / "step_timings.csv")
            return False, errors, step_timings
    if logs_dir is not None:
        write_step_timings_csv(step_timings, logs_dir.parent / "step_timings.csv")
    return True, [], step_timings


def run_pipeline_for_iteration_multiclass(
    project_json: Path,
    per_cancer_group: bool = False,
    logs_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[int, str, Literal["start", "end"]], None]] = None,
    detector_step_override: Optional[Path] = None,
    skip_centroid: bool = False,
    config: Optional["MonteCarloConfig"] = None,
) -> tuple[bool, List[str], List[Dict[str, Any]]]:
    """
    Monte Carlo stability iteration (multiclass template): methyl-centroid → methyl-detector
    (+ methyl-dmp-select when ``detection_mode=discovery_only``).

    """
    from .validator_metrics import write_step_timings_csv

    errors: List[str] = []
    step_timings: List[Dict[str, Any]] = []
    steps: List[Tuple[str, Callable[[], tuple[int, str, str]]]] = []
    if not skip_centroid:
        steps.append(
            ("methyl-centroid", lambda: run_centroid(project_json, centroid_step_overrides=None))
        )
    steps.append(
        (
            "methyl-detector",
            lambda: run_detector(
                project_json,
                per_cancer_group=per_cancer_group,
                detector_step_override=detector_step_override,
            ),
        ),
    )
    split_detector = _uses_discovery_only(project_json)
    if split_detector:
        steps.append(
            (
                "methyl-dmp-select",
                lambda: run_dmp_select(
                    project_json,
                    detector_step_override=detector_step_override,
                ),
            )
        )
    if config is not None and bool(getattr(config, "stability_gene_featurecuts_enabled", False)):
        steps.append(
            (
                "methyl-mapper",
                lambda: run_mapper(project_json, per_cancer_group=per_cancer_group, config=config),
            )
        )
        if split_detector:
            steps.append(
                (
                    "methyl-gene-select",
                    lambda: run_gene_select(
                        project_json,
                        config,
                        run_dir=Path(project_json).resolve().parent,
                    ),
                )
            )
        else:

            def _run_gene_fc_mc() -> tuple[int, str, str]:
                from .gene_featurecuts import run_gene_featurecuts_for_iteration

                return run_gene_featurecuts_for_iteration(project_json, config)

            steps.append(("gene-featurecuts", _run_gene_fc_mc))
    completed_seconds: List[float] = []
    total_steps = len(steps)
    for step_index, (step_name, run_fn) in enumerate(steps):
        if progress_callback is None:
            print(
                f"[mc] [{step_index + 1}/{total_steps}] running {step_name}...",
                file=sys.stderr,
                flush=True,
            )
        if progress_callback is not None:
            progress_callback(step_index, step_name, "start")
        t0 = time.perf_counter()
        rc, out, err = run_fn()
        duration_seconds = time.perf_counter() - t0
        if progress_callback is not None:
            progress_callback(step_index, step_name, "end")
        step_timings.append({
            "step_name": step_name,
            "duration_seconds": round(duration_seconds, 6),
            "return_code": rc,
        })
        if logs_dir is not None:
            log_path = logs_dir / f"{step_name}.log"
            _write_step_log(log_path, out, err)
        completed_seconds.append(duration_seconds)
        if progress_callback is None:
            remaining = total_steps - (step_index + 1)
            eta = _estimate_eta(completed_seconds, remaining)
            print(
                f"[mc] [{step_index + 1}/{total_steps}] {step_name} finished in "
                f"{_format_duration(duration_seconds)} (ETA {eta})",
                file=sys.stderr,
                flush=True,
            )
        if rc != 0:
            msg = f"{step_name} failed (exit {rc}). stderr: {err[:500] if err else 'none'}"
            errors.append(msg)
            if logs_dir is not None:
                write_step_timings_csv(step_timings, logs_dir.parent / "step_timings.csv")
            return False, errors, step_timings
    if logs_dir is not None:
        write_step_timings_csv(step_timings, logs_dir.parent / "step_timings.csv")
    return True, [], step_timings


def run_predictor_from_project(
    project_json: str | Path,
    output_dir: Optional[str | Path] = None,
) -> tuple[int, str, str]:
    """
    Run ``methyl-predictor --project`` using cohorts from resolved predictor config.
    With control/disease comparisons, the predictor CLI auto-enables per-comparison runs.
    """
    project_json = Path(project_json)
    cmd = ["methyl-predictor", "--project", str(project_json)]
    test_control_csv = project_json.parent / "test_control.csv"
    test_disease_csv = project_json.parent / "test_disease.csv"
    test_groups_json = project_json.parent / "test_groups.json"
    if test_control_csv.is_file() and test_disease_csv.is_file():
        cmd.extend(
            [
                "--test-control",
                str(test_control_csv),
                "--test-disease",
                str(test_disease_csv),
            ]
        )
    elif test_groups_json.is_file():
        cmd.extend(["--test-groups", str(test_groups_json)])
    if output_dir is not None:
        cmd.extend(["--output-dir", str(output_dir)])
    return run_cmd(cmd)


def run_pipeline_for_model(
    project_json: Path,
    logs_dir: Optional[Path] = None,
    predictor_output_dir: Optional[Path] = None,
    progress_callback: Optional[Callable[[int, str, Literal["start", "end"]], None]] = None,
    per_cancer_group: bool = False,
    config: Optional["MonteCarloConfig"] = None,
) -> Tuple[bool, List[str], List[Dict[str, Any]]]:
    """
    Production model step after freeze: methyl-classifier → methyl-predictor.

    Predictor resolves test sets from the production ``project.json`` (and optional
    ``--output-dir`` when ``predictor_output_dir`` is set).
    """
    from .validator_metrics import write_step_timings_csv

    from .analyte_guard import assert_training_analyte_match_for_model
    from .trainer_api import build_model_backend_steps

    enforce_analyte = bool(getattr(config, "enforce_training_analyte_match", False)) if config else False
    assert_training_analyte_match_for_model(
        project_json,
        enforce=enforce_analyte,
        production_dir=project_json.parent if project_json.name == "project.json" else None,
    )

    errors: List[str] = []
    step_timings: List[Dict[str, Any]] = []
    steps = build_model_backend_steps(
        project_json=project_json,
        predictor_output_dir=predictor_output_dir,
        config=config,
        per_cancer_group=per_cancer_group,
        run_classifier_fn=run_classifier,
        run_predictor_fn=run_predictor_from_project,
    )

    completed_seconds: List[float] = []
    total_steps = len(steps)
    for step_index, (step_name, run_fn) in enumerate(steps):
        print(
            f"[model] [{step_index + 1}/{total_steps}] running {step_name}...",
            file=sys.stderr,
            flush=True,
        )
        if progress_callback is not None:
            progress_callback(step_index, step_name, "start")
        t0 = time.perf_counter()
        rc, out, err = run_fn()
        duration_seconds = time.perf_counter() - t0
        if progress_callback is not None:
            progress_callback(step_index, step_name, "end")
        step_timings.append({
            "step_name": step_name,
            "duration_seconds": round(duration_seconds, 6),
            "return_code": rc,
        })
        if logs_dir is not None:
            log_path = logs_dir / f"{step_name}.log"
            _write_step_log(log_path, out, err)
        completed_seconds.append(duration_seconds)
        remaining = total_steps - (step_index + 1)
        eta = _estimate_eta(completed_seconds, remaining)
        print(
            f"[model] [{step_index + 1}/{total_steps}] {step_name} finished in "
            f"{_format_duration(duration_seconds)} (ETA {eta})",
            file=sys.stderr,
            flush=True,
        )
        if rc != 0:
            msg = f"{step_name} failed (exit {rc}). stderr: {err[:500] if err else 'none'}"
            errors.append(msg)
            if logs_dir is not None:
                write_step_timings_csv(step_timings, logs_dir.parent / "step_timings.csv")
            return False, errors, step_timings
    if logs_dir is not None:
        write_step_timings_csv(step_timings, logs_dir.parent / "step_timings.csv")
    return True, [], step_timings
