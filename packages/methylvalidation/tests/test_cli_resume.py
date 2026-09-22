from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from methyl_validation import cli
from methyl_validation.cli import (
    _deep_merge_dicts,
    _list_existing_run_numbers,
    _load_existing_step_timings,
    _resolve_resume_start_iteration,
)

_DEFAULT_VALIDATION_PROFILE = {
    "train_fraction": 0.8,
    "n_iterations": 2,
    "backend_profiles": {
        "ecdf": {"enabled": True, "params": {}},
        "tabular_sklearn": {
            "enabled": True,
            "params": {"tabular_methods": [{"method": "random_forest", "params": {}}]},
        },
        "generative_hybrid": {"enabled": True, "params": {}},
    },
}


def _write_mc_profile(tmp_path: Path, validation: dict | None = None) -> Path:
    payload = {**_DEFAULT_VALIDATION_PROFILE, **(validation or {})}
    profile_path = tmp_path / "mc.profile.json"
    profile_path.write_text(
        json.dumps({"pipelineProfile": "mc_test", "actionConfig": {"validation": payload}}),
        encoding="utf-8",
    )
    os.environ["METHYL_PROFILE"] = str(profile_path)
    return profile_path


@pytest.fixture(autouse=True)
def _mc_profile_env(tmp_path: Path) -> None:
    _write_mc_profile(tmp_path)


def _valid_production_project_text(
    tmp_path: Path, healthy_csv: Path, disease_csv: Path, out_dir: Path
) -> str:
    """Minimal project.json that passes load_project and infer_monte_carlo_layout (2 flat groups)."""
    return json.dumps(
        {
            "project_name": "x",
            "output_base": str(out_dir),
            "samples_base_path": str(tmp_path),
            "groups": [
                {"label": "healthy", "sample_paths": [str(healthy_csv)]},
                {"label": "disease", "sample_paths": [str(disease_csv)]},
            ],
        }
    )


def test_resolve_resume_start_iteration_auto_and_explicit():
    assert _resolve_resume_start_iteration(None, n_iterations=10, existing_runs=[]) == 0
    assert _resolve_resume_start_iteration(0, n_iterations=10, existing_runs=[]) == 0
    assert _resolve_resume_start_iteration(0, n_iterations=10, existing_runs=[1, 2, 5]) == 4
    assert _resolve_resume_start_iteration(3, n_iterations=10, existing_runs=[1, 2]) == 2


def test_resolve_resume_start_iteration_validates_bounds():
    with pytest.raises(ValueError, match=">= 1"):
        _resolve_resume_start_iteration(-1, n_iterations=5, existing_runs=[1, 2])
    with pytest.raises(ValueError, match="<= n_iterations"):
        _resolve_resume_start_iteration(6, n_iterations=5, existing_runs=[1, 2])


def test_list_existing_run_numbers_and_load_step_timings(tmp_path: Path):
    root = tmp_path / "mc"
    root.mkdir()
    for run_name in ("run_0001", "run_0002", "run_0004", "notes"):
        (root / run_name).mkdir(exist_ok=True)
    assert _list_existing_run_numbers(root) == [1, 2, 4]

    timings = root / "step_timings.csv"
    timings.write_text(
        "step_name,duration_seconds,return_code,run_id,run_dir,n_train_samples,n_val_samples\n"
        "methyl-centroid,4.2,0,run_0001,/tmp/run_0001,10,4\n"
        "methyl-detector,8.0,0,run_0003,/tmp/run_0003,10,4\n",
        encoding="utf-8",
    )
    kept = _load_existing_step_timings(
        timings,
        keep_until_iteration_exclusive=3,
    )
    assert len(kept) == 1
    assert kept[0]["run_id"] == "run_0001"
    assert isinstance(kept[0]["duration_seconds"], float)
    assert isinstance(kept[0]["return_code"], int)


def test_deep_merge_dicts_prefers_updates_and_preserves_other_keys():
    base = {"detection": {"min_samples_pct": 0.8, "debug": True}, "predictor": {"controls": {"foo": 1}}}
    updates = {"detection": {"min_samples_pct": 0.0, "min_samples_abs": 5}, "predictor": {"model_path": "/tmp/m.pkl"}}
    merged = _deep_merge_dicts(base, updates)
    assert merged["detection"]["min_samples_pct"] == 0.0
    assert merged["detection"]["min_samples_abs"] == 5
    assert merged["detection"]["debug"] is True
    assert merged["predictor"]["controls"] == {"foo": 1}
    assert merged["predictor"]["model_path"] == "/tmp/m.pkl"


def test_post_model_validation_requires_production_project(tmp_path: Path, monkeypatch, capsys):
    project = tmp_path / "project.json"
    project.write_text(
        """
{
  "project_name": "x",
  "output_base": "/tmp/out",
  "samples_base_path": "/tmp/samples",
  "groups": [
    {"label": "healthy", "sample_paths": ["healthy.csv"]},
    {"label": "disease", "sample_paths": ["disease.csv"]}
  ]}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        ["methyl-validation", "--project", str(project), "--post-model-validation"],
    )
    with pytest.raises(SystemExit) as ex:
        cli.main()
    assert ex.value.code == 1
    err = capsys.readouterr().err
    assert "requires production project" in err


def test_model_mc_all_requires_model_mc(tmp_path: Path, monkeypatch, capsys):
    h = tmp_path / "healthy.csv"
    d = tmp_path / "disease.csv"
    h.write_text("sample\nH1\n", encoding="utf-8")
    d.write_text("sample\nD1\n", encoding="utf-8")
    project = tmp_path / "project.json"
    project.write_text(
        f"""
{{
  "project_name": "x",
  "output_base": "{tmp_path.as_posix()}",
  "samples_base_path": "{tmp_path.as_posix()}",
  "groups": [
    {{"label": "healthy", "sample_paths": ["{h.as_posix()}"]}},
    {{"label": "disease", "sample_paths": ["{d.as_posix()}"]}}
  ]}}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sys.argv",
        ["methyl-validation", "--project", str(project), "--model-mc-all"],
    )
    with pytest.raises(SystemExit) as ex:
        cli.main()
    assert ex.value.code == 1
    err = capsys.readouterr().err
    assert "--model-mc-all requires --model-mc" in err


def test_skip_detection_runs_stability_only(tmp_path: Path, monkeypatch):
    h = tmp_path / "healthy.csv"
    d = tmp_path / "disease.csv"
    h.write_text("sample\nH1\n", encoding="utf-8")
    d.write_text("sample\nD1\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    project = tmp_path / "project.json"
    project.write_text(
        f"""
{{
  "project_name": "x",
  "output_base": "{out_dir.as_posix()}",
  "samples_base_path": "{tmp_path.as_posix()}",
  "groups": [
    {{"label": "healthy", "sample_paths": ["{h.as_posix()}"]}},
    {{"label": "disease", "sample_paths": ["{d.as_posix()}"]}}
  ]}}
""".strip(),
        encoding="utf-8",
    )

    calls: dict[str, object] = {}

    def _fake_stability(**kwargs):
        calls["kwargs"] = kwargs
        return {
            "output_dir": str(out_dir / "x" / "monte_carlo_runs" / "stability"),
            "dmp_stability": {"stable_dmps_at_threshold": 1},
            "gene_stability": {"stable_genes_at_threshold": 0},
            "tiered_stability_enabled": False,
        }

    monkeypatch.setattr(cli, "run_stability_analysis", _fake_stability)
    monkeypatch.setattr(
        "sys.argv",
        ["methyl-validation", "--project", str(project), "--stability", "--skip-detection"],
    )
    cli.main()
    assert "kwargs" in calls
    kwargs = calls["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["monte_carlo_runs_root"] == out_dir / "x" / "monte_carlo_runs"


def test_skip_detection_allowed_for_freeze_and_forwarded(tmp_path: Path, monkeypatch):
    h = tmp_path / "healthy.csv"
    d = tmp_path / "disease.csv"
    h.write_text("sample\nH1\n", encoding="utf-8")
    d.write_text("sample\nD1\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    project = tmp_path / "project.json"
    project.write_text(_valid_production_project_text(tmp_path, h, d, out_dir), encoding="utf-8")

    mc_root = out_dir / "x" / "monte_carlo_runs"
    stable_dir = mc_root / "stability"
    stable_dir.mkdir(parents=True, exist_ok=True)
    (stable_dir / "stable_dmps_production.csv").write_text(
        "chromosome,position,context,effect_size\n1,100,CG,0.2\n",
        encoding="utf-8",
    )

    calls: dict[str, object] = {}

    def _fake_freeze(**kwargs):
        calls["kwargs"] = kwargs
        prod_dir = mc_root / "production"
        prod_dir.mkdir(parents=True, exist_ok=True)
        return {
            "success": True,
            "output_dir": str(prod_dir),
            "errors": [],
            "timings": [],
        }

    monkeypatch.setattr(cli, "freeze_production_model", _fake_freeze)
    monkeypatch.setattr(
        "sys.argv",
        ["methyl-validation", "--project", str(project), "--freeze", "--skip-detection"],
    )
    cli.main()
    assert "kwargs" in calls
    kwargs = calls["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["skip_detection"] is True
    assert kwargs["skip_centroid"] is True


def test_stability_early_stop_breaks_loop_and_writes_diagnostics(tmp_path: Path, monkeypatch):
    _write_mc_profile(
        tmp_path,
        {
            "n_iterations": 6,
            "run_stability": True,
            "stability_early_stop_enabled": True,
            "stability_min_iterations": 2,
            "stability_convergence_window": 1,
            "stability_convergence_jaccard": 0.95,
            "stability_convergence_max_size_delta": 0.2,
            "stability_convergence_patience": 2,
        },
    )
    h = tmp_path / "healthy.csv"
    d = tmp_path / "disease.csv"
    h.write_text("sample\nH1\nH2\n", encoding="utf-8")
    d.write_text("sample\nD1\nD2\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    project = tmp_path / "project.json"
    project.write_text(
        f"""
{{
  "project_name": "x",
  "output_base": "{out_dir.as_posix()}",
  "samples_base_path": "{tmp_path.as_posix()}",
  "groups": [
    {{"label": "healthy", "sample_paths": ["{h.as_posix()}"]}},
    {{"label": "disease", "sample_paths": ["{d.as_posix()}"]}}
  ]}}
""".strip(),
        encoding="utf-8",
    )

    state = {"iterations_run": 0, "checkpoints": 0, "stability_kwargs": None}

    monkeypatch.setattr(cli, "infer_monte_carlo_layout", lambda *_args, **_kwargs: "binary")
    monkeypatch.setattr(cli, "load_and_resolve_sample_paths", lambda *_args, **_kwargs: (["H1", "H2"], ["D1", "D2"]))
    monkeypatch.setattr(cli, "stratified_split", lambda *_args, **_kwargs: (["H1"], ["D1"], ["H2"], ["D2"]))
    monkeypatch.setattr(cli, "write_detector_featurecuts_override", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "load_project",
        lambda _path: SimpleNamespace(
            get_comparisons=lambda: [SimpleNamespace(control_group="healthy", disease_group="disease")]
        ),
    )

    def _fake_generate_run_project(*args, **kwargs):
        run_dir = Path(args[1])
        run_dir.mkdir(parents=True, exist_ok=True)
        project_path = run_dir / "project.json"
        project_path.write_text("{}", encoding="utf-8")
        val_control_csv = run_dir / "val_control.csv"
        val_disease_csv = run_dir / "val_disease.csv"
        val_control_csv.write_text("sample\nH2\n", encoding="utf-8")
        val_disease_csv.write_text("sample\nD2\n", encoding="utf-8")
        return project_path, None, None, val_control_csv, val_disease_csv, None, None

    def _fake_run_pipeline_for_iteration(*_args, **kwargs):
        state["iterations_run"] += 1
        logs_dir = Path(kwargs["logs_dir"])
        logs_dir.mkdir(parents=True, exist_ok=True)
        return True, [], []

    def _fake_evaluate_dmp_stability_convergence(*_args, **_kwargs):
        state["checkpoints"] += 1
        return {
            "eligible_for_check": True,
            "converged_checkpoint": True,
            "jaccard": 1.0,
            "relative_size_delta": 0.0,
            "n_runs_analyzed": state["checkpoints"],
        }

    def _fake_stability(**kwargs):
        state["stability_kwargs"] = kwargs
        return {
            "output_dir": str(out_dir / "x" / "monte_carlo_runs" / "stability"),
            "dmp_stability": {"stable_dmps_at_threshold": 1},
            "gene_stability": {"stable_genes_at_threshold": 0},
            "tiered_stability_enabled": False,
        }

    monkeypatch.setattr(cli, "generate_run_project", _fake_generate_run_project)
    monkeypatch.setattr(cli, "run_pipeline_for_iteration", _fake_run_pipeline_for_iteration)
    monkeypatch.setattr(cli, "run_stability_analysis", _fake_stability)
    monkeypatch.setattr(cli, "iteration_scalar_metrics_from_run_dir", lambda _run_dir: {"balanced_accuracy": 0.91})
    monkeypatch.setattr(cli, "evaluate_dmp_stability_convergence", _fake_evaluate_dmp_stability_convergence)
    monkeypatch.setattr(
        "sys.argv",
        ["methyl-validation", "--project", str(project), "--stability"],
    )

    cli.main()
    assert state["iterations_run"] == 2
    assert state["checkpoints"] == 2
    stability_kwargs = state["stability_kwargs"]
    assert isinstance(stability_kwargs, dict)
    early = stability_kwargs["convergence_diagnostics"]
    assert early["enabled"] is True
    assert early["triggered"] is True
    assert early["stopped_after_iteration"] == 2
    assert len(early["checkpoint_history"]) == 2


def test_stability_early_stop_disabled_keeps_full_iteration_budget(tmp_path: Path, monkeypatch):
    _write_mc_profile(
        tmp_path,
        {
            "n_iterations": 3,
            "run_stability": True,
            "stability_early_stop_enabled": False,
        },
    )
    h = tmp_path / "healthy.csv"
    d = tmp_path / "disease.csv"
    h.write_text("sample\nH1\nH2\n", encoding="utf-8")
    d.write_text("sample\nD1\nD2\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    project = tmp_path / "project.json"
    project.write_text(
        f"""
{{
  "project_name": "x",
  "output_base": "{out_dir.as_posix()}",
  "samples_base_path": "{tmp_path.as_posix()}",
  "groups": [
    {{"label": "healthy", "sample_paths": ["{h.as_posix()}"]}},
    {{"label": "disease", "sample_paths": ["{d.as_posix()}"]}}
  ]}}
""".strip(),
        encoding="utf-8",
    )

    state = {"iterations_run": 0}
    monkeypatch.setattr(cli, "infer_monte_carlo_layout", lambda *_args, **_kwargs: "binary")
    monkeypatch.setattr(cli, "load_and_resolve_sample_paths", lambda *_args, **_kwargs: (["H1", "H2"], ["D1", "D2"]))
    monkeypatch.setattr(cli, "stratified_split", lambda *_args, **_kwargs: (["H1"], ["D1"], ["H2"], ["D2"]))
    monkeypatch.setattr(cli, "write_detector_featurecuts_override", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "load_project",
        lambda _path: SimpleNamespace(
            get_comparisons=lambda: [SimpleNamespace(control_group="healthy", disease_group="disease")]
        ),
    )

    def _fake_generate_run_project(*args, **kwargs):
        run_dir = Path(args[1])
        run_dir.mkdir(parents=True, exist_ok=True)
        project_path = run_dir / "project.json"
        project_path.write_text("{}", encoding="utf-8")
        val_control_csv = run_dir / "val_control.csv"
        val_disease_csv = run_dir / "val_disease.csv"
        val_control_csv.write_text("sample\nH2\n", encoding="utf-8")
        val_disease_csv.write_text("sample\nD2\n", encoding="utf-8")
        return project_path, None, None, val_control_csv, val_disease_csv, None, None

    def _fake_run_pipeline_for_iteration(*_args, **kwargs):
        state["iterations_run"] += 1
        logs_dir = Path(kwargs["logs_dir"])
        logs_dir.mkdir(parents=True, exist_ok=True)
        return True, [], []

    monkeypatch.setattr(cli, "generate_run_project", _fake_generate_run_project)
    monkeypatch.setattr(cli, "run_pipeline_for_iteration", _fake_run_pipeline_for_iteration)
    monkeypatch.setattr(cli, "iteration_scalar_metrics_from_run_dir", lambda _run_dir: {"balanced_accuracy": 0.91})
    monkeypatch.setattr(
        cli,
        "evaluate_dmp_stability_convergence",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("early-stop evaluator should not be called")),
    )
    monkeypatch.setattr(
        cli,
        "run_stability_analysis",
        lambda **_kwargs: {
            "output_dir": str(out_dir / "x" / "monte_carlo_runs" / "stability"),
            "dmp_stability": {"stable_dmps_at_threshold": 1},
            "gene_stability": {"stable_genes_at_threshold": 0},
            "tiered_stability_enabled": False,
        },
    )
    monkeypatch.setattr(
        "sys.argv",
        ["methyl-validation", "--project", str(project), "--stability"],
    )

    cli.main()
    assert state["iterations_run"] == 3


def test_model_mc_all_uses_shared_stage(tmp_path: Path, monkeypatch):
    h = tmp_path / "healthy.csv"
    d = tmp_path / "disease.csv"
    h.write_text("sample\nH1\nH2\n", encoding="utf-8")
    d.write_text("sample\nD1\nD2\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    production_dir = out_dir / "x" / "monte_carlo_runs" / "production"
    production_dir.mkdir(parents=True, exist_ok=True)
    (production_dir / "project.json").write_text(
        _valid_production_project_text(tmp_path, h, d, out_dir), encoding="utf-8"
    )
    project = tmp_path / "project.json"
    project.write_text(
        f"""
{{
  "project_name": "x",
  "output_base": "{out_dir.as_posix()}",
  "samples_base_path": "{tmp_path.as_posix()}",
  "groups": [
    {{"label": "healthy", "sample_paths": ["{h.as_posix()}"]}},
    {{"label": "disease", "sample_paths": ["{d.as_posix()}"]}}
  ]}}
""".strip(),
        encoding="utf-8",
    )

    calls = {"shared": 0, "backends": []}

    def _fake_shared(**kwargs):
        calls["shared"] += 1
        return [
            {
                "iteration": 1,
                "run_id": "run_0001",
                "run_dir": str(tmp_path / "shared" / "run_0001"),
                "project_json": str(tmp_path / "shared" / "run_0001" / "project.json"),
                "n_train_samples": 2,
                "n_val_samples": 2,
            }
        ]

    def _fake_backend(**kwargs):
        calls["backends"].append(kwargs["backend"])

    monkeypatch.setattr(cli, "_build_model_mc_shared_runs", _fake_shared)
    monkeypatch.setattr(cli, "_run_model_mc_backend_from_shared_runs", _fake_backend)
    monkeypatch.setattr(
        "sys.argv",
        ["methyl-validation", "--project", str(project), "--model-mc", "--model-mc-all"],
    )
    cli.main()
    assert calls["shared"] == 1
    assert calls["backends"] == ["ecdf", "tabular_sklearn", "generative_hybrid"]


def test_select_best_model_ignores_shared_directory(tmp_path: Path, monkeypatch):
    h = tmp_path / "healthy.csv"
    d = tmp_path / "disease.csv"
    h.write_text("sample\nH1\nH2\n", encoding="utf-8")
    d.write_text("sample\nD1\nD2\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    model_mc_root = out_dir / "x" / "monte_carlo_runs" / "model_mc"
    production_dir = out_dir / "x" / "monte_carlo_runs" / "production"
    production_dir.mkdir(parents=True, exist_ok=True)
    (production_dir / "project.json").write_text(
        _valid_production_project_text(tmp_path, h, d, out_dir), encoding="utf-8"
    )
    (model_mc_root / "shared").mkdir(parents=True, exist_ok=True)
    for backend in ("ecdf", "tabular_sklearn", "generative_hybrid"):
        root = model_mc_root / backend
        root.mkdir(parents=True, exist_ok=True)
        (root / "metrics_summary.json").write_text(
            '{"balanced_accuracy": {"mean": 0.7, "percentiles": {"p50": 0.7}}}',
            encoding="utf-8",
        )
    project = tmp_path / "project.json"
    project.write_text(
        f"""
{{
  "project_name": "x",
  "output_base": "{out_dir.as_posix()}",
  "samples_base_path": "{tmp_path.as_posix()}",
  "groups": [
    {{"label": "healthy", "sample_paths": ["{h.as_posix()}"]}},
    {{"label": "disease", "sample_paths": ["{d.as_posix()}"]}}
  ]}}
""".strip(),
        encoding="utf-8",
    )

    captured: dict[str, list[str]] = {}

    def _fake_rank(**kwargs):
        captured["backends"] = list(kwargs["backends"])
        return [
            {"backend": "ecdf", "metric": "balanced_accuracy", "stat": "median", "score": 0.8, "rank": 1}
        ]

    monkeypatch.setattr(cli, "_write_backend_ranking", _fake_rank)
    monkeypatch.setattr(
        cli,
        "build_production_model",
        lambda **kwargs: {"success": True, "output_dir": str(production_dir)},
    )
    monkeypatch.setattr(
        "sys.argv",
        ["methyl-validation", "--project", str(project), "--select-best-model", "--model-mc-all"],
    )
    cli.main()
    assert set(captured.get("backends", [])) == {"ecdf", "tabular_sklearn", "generative_hybrid"}


def test_model_mc_single_backend_reuses_existing_shared_runs(tmp_path: Path, monkeypatch):
    h = tmp_path / "healthy.csv"
    d = tmp_path / "disease.csv"
    h.write_text("sample\nH1\nH2\n", encoding="utf-8")
    d.write_text("sample\nD1\nD2\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    production_dir = out_dir / "x" / "monte_carlo_runs" / "production"
    production_dir.mkdir(parents=True, exist_ok=True)
    (production_dir / "project.json").write_text(
        _valid_production_project_text(tmp_path, h, d, out_dir), encoding="utf-8"
    )
    shared_run_dir = out_dir / "x" / "monte_carlo_runs" / "model_mc" / "shared" / "run_0001"
    shared_run_dir.mkdir(parents=True, exist_ok=True)
    (shared_run_dir / "project.json").write_text("{}", encoding="utf-8")
    project = tmp_path / "project.json"
    project.write_text(
        f"""
{{
  "project_name": "x",
  "output_base": "{out_dir.as_posix()}",
  "samples_base_path": "{tmp_path.as_posix()}",
  "groups": [
    {{"label": "healthy", "sample_paths": ["{h.as_posix()}"]}},
    {{"label": "disease", "sample_paths": ["{d.as_posix()}"]}}
  ]}}
""".strip(),
        encoding="utf-8",
    )

    calls = {"shared_runner": 0, "legacy_runner": 0}

    def _fake_shared_runner(**kwargs):
        calls["shared_runner"] += 1

    def _fake_legacy_runner(**kwargs):
        calls["legacy_runner"] += 1

    monkeypatch.setattr(cli, "_run_model_mc_backend_from_shared_runs", _fake_shared_runner)
    monkeypatch.setattr(cli, "_run_model_mc_backend", _fake_legacy_runner)
    monkeypatch.setattr(
        "sys.argv",
        [
            "methyl-validation",
            "--project",
            str(project),
            "--model-mc",
            "--model-backend",
            "generative_hybrid",
        ],
    )
    cli.main()
    assert calls["shared_runner"] == 1
    assert calls["legacy_runner"] == 0


def test_build_model_mc_shared_runs_symlinks_reusable_primary_runs(tmp_path: Path, monkeypatch):
    primary_root = tmp_path / "primary_mc"
    run1 = primary_root / "run_0001"
    (run1 / "detections" / "all" / "pca_pca1").mkdir(parents=True, exist_ok=True)
    (run1 / "centroids").mkdir(parents=True, exist_ok=True)
    (run1 / "project.json").write_text("{}", encoding="utf-8")
    (primary_root / "step_timings.csv").write_text(
        "step_name,duration_seconds,return_code,run_id,run_dir,n_train_samples,n_val_samples\n"
        "methyl-detector,12.0,0,run_0001,/tmp/run_0001,8,2\n",
        encoding="utf-8",
    )

    calls = {"generated_project": 0, "ran_pipeline": 0, "run_dir_existed_at_generate": None}

    def _fake_resolve_iteration_split(**kwargs):
        return (["h1", "h2"], ["d1", "d2"], ["h3"], ["d3"]), "reused"

    def _fake_generate_run_project(
        base_project_path,
        run_dir,
        run_id,
        output_base,
        train_control,
        train_disease,
        val_control,
        val_disease,
        samples_base_path,
    ):
        calls["generated_project"] += 1
        run_dir = Path(run_dir)
        calls["run_dir_existed_at_generate"] = run_dir.is_dir()
        run_dir.mkdir(parents=True, exist_ok=True)
        project_path = run_dir / "project.json"
        project_path.write_text("{}", encoding="utf-8")
        return (
            project_path,
            run_dir / "training_control.csv",
            run_dir / "training_disease.csv",
            run_dir / "testing_control.csv",
            run_dir / "testing_disease.csv",
            None,
            None,
        )

    def _forbidden_run_pipeline(*args, **kwargs):
        calls["ran_pipeline"] += 1
        raise AssertionError("run_pipeline_for_iteration should not be called when reusable run exists")

    monkeypatch.setattr(cli, "resolve_iteration_split", _fake_resolve_iteration_split)
    monkeypatch.setattr(cli, "generate_run_project", _fake_generate_run_project)
    monkeypatch.setattr(cli, "run_pipeline_for_iteration", _forbidden_run_pipeline)

    config = SimpleNamespace(
        n_iterations=1,
        train_fraction=0.8,
        seed=42,
        samples_base_path=str(tmp_path),
        abort_on_step_failure=True,
    )
    shared_root = tmp_path / "model_mc" / "shared"
    rows = cli._build_model_mc_shared_runs(
        base_project_for_runs=tmp_path / "production_project.json",
        config=config,
        layout="binary",
        cohort_paths_list=[("healthy", ["h1", "h2", "h3"]), ("disease", ["d1", "d2", "d3"])],
        cohort_labels=["healthy", "disease"],
        control_paths=["h1", "h2", "h3"],
        disease_paths=["d1", "d2", "d3"],
        shared_root=shared_root,
        resume_arg=None,
        per_cancer_group=False,
        primary_monte_carlo_runs_root=primary_root,
        require_artifact_reuse=True,
    )

    assert len(rows) == 1
    assert rows[0]["run_id"] == "run_0001"
    linked_run = shared_root / "run_0001"
    assert linked_run.is_dir()
    assert not linked_run.is_symlink()
    assert (linked_run / "project.json").is_file()
    assert (linked_run / "centroids").is_symlink()
    assert (linked_run / "detections").is_symlink()
    assert (linked_run / "centroids").resolve() == (run1 / "centroids").resolve()
    assert (linked_run / "detections").resolve() == (run1 / "detections").resolve()
    assert calls["generated_project"] == 1
    assert calls["ran_pipeline"] == 0
    assert calls["run_dir_existed_at_generate"] is True


def test_build_model_mc_shared_runs_reuses_centroids_when_detections_incompatible(
    tmp_path: Path, monkeypatch
):
    primary_root = tmp_path / "primary_mc"
    run1 = primary_root / "run_0001"
    (run1 / "detections" / "all" / "pca_pca1").mkdir(parents=True, exist_ok=True)
    (run1 / "centroids").mkdir(parents=True, exist_ok=True)
    (run1 / "project.json").write_text(
        json.dumps({"actionConfig": {"detection": {"detection_mode": "discovery_only"}}}),
        encoding="utf-8",
    )
    production_project = tmp_path / "production_project.json"
    production_project.write_text(
        json.dumps(
            {
                "actionConfig": {
                    "detection": {
                        "detection_mode": "fixed",
                        "fixed_dmp_panel": "/tmp/stable_dmps.csv",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    calls: dict[str, object] = {
        "generated_project": 0,
        "ran_pipeline": 0,
        "skip_centroid": None,
        "generate_saw_stale_mapper": None,
        "generate_saw_stale_detections": None,
        "run_dir_existed_at_generate": None,
    }

    def _fake_resolve_iteration_split(**kwargs):
        return (["h1", "h2"], ["d1", "d2"], ["h3"], ["d3"]), "reused"

    def _fake_generate_run_project(
        base_project_path,
        run_dir,
        run_id,
        output_base,
        train_control,
        train_disease,
        val_control,
        val_disease,
        samples_base_path,
        **kwargs,
    ):
        calls["generated_project"] = int(calls["generated_project"]) + 1
        run_dir = Path(run_dir)
        calls["run_dir_existed_at_generate"] = run_dir.is_dir()
        calls["generate_saw_stale_mapper"] = (run_dir / "mapper" / "stale.txt").exists()
        calls["generate_saw_stale_detections"] = (run_dir / "detections" / "stale.txt").exists()
        run_dir.mkdir(parents=True, exist_ok=True)
        project_path = run_dir / "project.json"
        project_path.write_text(production_project.read_text(encoding="utf-8"), encoding="utf-8")
        return (
            project_path,
            run_dir / "train_control.csv",
            run_dir / "train_disease.csv",
            run_dir / "test_control.csv",
            run_dir / "test_disease.csv",
            None,
            None,
        )

    def _fake_run_pipeline(project_path, *args, **kwargs):
        calls["ran_pipeline"] = int(calls["ran_pipeline"]) + 1
        calls["skip_centroid"] = kwargs.get("skip_centroid")
        detections = Path(project_path).parent / "detections"
        detections.mkdir(parents=True, exist_ok=True)
        return True, [], [{"step_name": "methyl-detector", "duration_seconds": 1.0, "return_code": 0}]

    monkeypatch.setattr(cli, "resolve_iteration_split", _fake_resolve_iteration_split)
    monkeypatch.setattr(cli, "generate_run_project", _fake_generate_run_project)
    monkeypatch.setattr(cli, "run_pipeline_for_iteration", _fake_run_pipeline)

    config = SimpleNamespace(
        n_iterations=1,
        train_fraction=0.8,
        seed=42,
        samples_base_path=str(tmp_path),
        abort_on_step_failure=True,
    )
    shared_root = tmp_path / "model_mc" / "shared"
    stale_run = shared_root / "run_0001"
    (stale_run / "mapper").mkdir(parents=True, exist_ok=True)
    (stale_run / "mapper" / "stale.txt").write_text("leftover mapper", encoding="utf-8")
    (stale_run / "detections").mkdir(parents=True, exist_ok=True)
    (stale_run / "detections" / "stale.txt").write_text("leftover detections", encoding="utf-8")
    (stale_run / "project.json").write_text(
        json.dumps({"actionConfig": {"detection": {"detection_mode": "discovery_only"}}}),
        encoding="utf-8",
    )
    rows = cli._build_model_mc_shared_runs(
        base_project_for_runs=production_project,
        config=config,
        layout="binary",
        cohort_paths_list=[("healthy", ["h1", "h2", "h3"]), ("disease", ["d1", "d2", "d3"])],
        cohort_labels=["healthy", "disease"],
        control_paths=["h1", "h2", "h3"],
        disease_paths=["d1", "d2", "d3"],
        shared_root=shared_root,
        resume_arg=None,
        per_cancer_group=False,
        primary_monte_carlo_runs_root=primary_root,
        require_artifact_reuse=False,
    )

    assert len(rows) == 1
    linked_run = shared_root / "run_0001"
    assert (linked_run / "centroids").is_symlink()
    assert (linked_run / "centroids").resolve() == (run1 / "centroids").resolve()
    assert (linked_run / "detections").is_dir()
    assert not (linked_run / "detections").is_symlink()
    assert not (linked_run / "detections" / "stale.txt").exists()
    assert not (linked_run / "mapper").exists()
    assert calls["run_dir_existed_at_generate"] is True
    assert calls["generate_saw_stale_mapper"] is False
    assert calls["generate_saw_stale_detections"] is False
    assert calls["ran_pipeline"] == 1
    assert calls["skip_centroid"] is True


def test_build_model_mc_shared_runs_strict_reuse_rejects_incompatible_detections(
    tmp_path: Path, monkeypatch
):
    primary_root = tmp_path / "primary_mc"
    run1 = primary_root / "run_0001"
    (run1 / "centroids").mkdir(parents=True)
    (run1 / "detections").mkdir()
    (run1 / "project.json").write_text(
        json.dumps({"actionConfig": {"detection": {"detection_mode": "discovery_only"}}}),
        encoding="utf-8",
    )
    production_project = tmp_path / "production_project.json"
    production_project.write_text(
        json.dumps(
            {
                "actionConfig": {
                    "detection": {
                        "detection_mode": "fixed",
                        "fixed_dmp_panel": "/tmp/stable_dmps.csv",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli,
        "resolve_iteration_split",
        lambda **_kwargs: (
            (["h1", "h2"], ["d1", "d2"], ["h3"], ["d3"]),
            "reused",
        ),
    )

    def _forbidden_run_pipeline(*_args, **_kwargs):
        raise AssertionError("strict reuse must fail before centroid/detector execution")

    monkeypatch.setattr(cli, "run_pipeline_for_iteration", _forbidden_run_pipeline)

    config = SimpleNamespace(
        n_iterations=1,
        train_fraction=0.8,
        seed=42,
        samples_base_path=str(tmp_path),
        abort_on_step_failure=True,
    )

    with pytest.raises(RuntimeError, match="detection contract"):
        cli._build_model_mc_shared_runs(
            base_project_for_runs=production_project,
            config=config,
            layout="binary",
            cohort_paths_list=[
                ("healthy", ["h1", "h2", "h3"]),
                ("disease", ["d1", "d2", "d3"]),
            ],
            cohort_labels=["healthy", "disease"],
            control_paths=["h1", "h2", "h3"],
            disease_paths=["d1", "d2", "d3"],
            shared_root=tmp_path / "model_mc" / "shared",
            resume_arg=None,
            per_cancer_group=False,
            primary_monte_carlo_runs_root=primary_root,
            require_artifact_reuse=True,
        )


def test_build_model_mc_shared_runs_strict_reuse_rejects_missing_artifacts(
    tmp_path: Path, monkeypatch
):
    primary_root = tmp_path / "primary_mc"

    monkeypatch.setattr(
        cli,
        "resolve_iteration_split",
        lambda **_kwargs: (
            (["h1", "h2"], ["d1", "d2"], ["h3"], ["d3"]),
            "reused",
        ),
    )

    def _forbidden_run_pipeline(*_args, **_kwargs):
        raise AssertionError("strict reuse must fail before centroid/detector execution")

    monkeypatch.setattr(cli, "run_pipeline_for_iteration", _forbidden_run_pipeline)

    config = SimpleNamespace(
        n_iterations=1,
        train_fraction=0.8,
        seed=42,
        samples_base_path=str(tmp_path),
        abort_on_step_failure=True,
    )

    with pytest.raises(RuntimeError, match="Centroid/detector recomputation is disabled"):
        cli._build_model_mc_shared_runs(
            base_project_for_runs=tmp_path / "production_project.json",
            config=config,
            layout="binary",
            cohort_paths_list=[
                ("healthy", ["h1", "h2", "h3"]),
                ("disease", ["d1", "d2", "d3"]),
            ],
            cohort_labels=["healthy", "disease"],
            control_paths=["h1", "h2", "h3"],
            disease_paths=["d1", "d2", "d3"],
            shared_root=tmp_path / "model_mc" / "shared",
            resume_arg=None,
            per_cancer_group=False,
            primary_monte_carlo_runs_root=primary_root,
            require_artifact_reuse=True,
        )


def test_build_model_mc_shared_runs_strict_reuse_rejects_generated_split(
    tmp_path: Path, monkeypatch
):
    primary_root = tmp_path / "primary_mc"
    run1 = primary_root / "run_0001"
    (run1 / "centroids").mkdir(parents=True)
    (run1 / "detections").mkdir()
    (run1 / "project.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "resolve_iteration_split",
        lambda **_kwargs: (
            (["h1", "h2"], ["d1", "d2"], ["h3"], ["d3"]),
            "generated",
        ),
    )

    config = SimpleNamespace(
        n_iterations=1,
        train_fraction=0.8,
        seed=42,
        samples_base_path=str(tmp_path),
        abort_on_step_failure=True,
    )

    with pytest.raises(RuntimeError, match="compatible primary split"):
        cli._build_model_mc_shared_runs(
            base_project_for_runs=tmp_path / "production_project.json",
            config=config,
            layout="binary",
            cohort_paths_list=[
                ("healthy", ["h1", "h2", "h3"]),
                ("disease", ["d1", "d2", "d3"]),
            ],
            cohort_labels=["healthy", "disease"],
            control_paths=["h1", "h2", "h3"],
            disease_paths=["d1", "d2", "d3"],
            shared_root=tmp_path / "model_mc" / "shared",
            resume_arg=None,
            per_cancer_group=False,
            primary_monte_carlo_runs_root=primary_root,
            require_artifact_reuse=True,
        )


def test_run_model_mc_backend_reuses_primary_centroid_detector_artifacts(tmp_path: Path, monkeypatch):
    primary_root = tmp_path / "primary_mc"
    run1 = primary_root / "run_0001"
    (run1 / "detections" / "all" / "pca_pca1").mkdir(parents=True, exist_ok=True)
    (run1 / "centroids").mkdir(parents=True, exist_ok=True)
    (run1 / "project.json").write_text("{}", encoding="utf-8")
    (primary_root / "step_timings.csv").write_text(
        "step_name,duration_seconds,return_code,run_id,run_dir,n_train_samples,n_val_samples\n"
        "methyl-detector,9.0,0,run_0001,/tmp/run_0001,8,2\n",
        encoding="utf-8",
    )

    calls = {"generated_project": 0, "ran_detector_stage": 0, "ran_model_stage": 0}

    def _fake_resolve_iteration_split(**kwargs):
        return (["h1", "h2"], ["d1", "d2"], ["h3"], ["d3"]), "reused"

    def _fake_generate_run_project(
        base_project_path,
        run_dir,
        run_id,
        output_base,
        train_control,
        train_disease,
        val_control,
        val_disease,
        samples_base_path,
        **kwargs,
    ):
        calls["generated_project"] += 1
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        project_path = run_dir / "project.json"
        project_path.write_text("{}", encoding="utf-8")
        return (
            project_path,
            run_dir / "training_control.csv",
            run_dir / "training_disease.csv",
            run_dir / "testing_control.csv",
            run_dir / "testing_disease.csv",
            None,
            None,
        )

    def _forbidden_run_pipeline(*args, **kwargs):
        calls["ran_detector_stage"] += 1
        raise AssertionError("run_pipeline_for_iteration should not be called when reusable run exists")

    def _fake_run_pipeline_for_model(**kwargs):
        calls["ran_model_stage"] += 1
        pred_dir = Path(kwargs["predictor_output_dir"])
        pred_dir.mkdir(parents=True, exist_ok=True)
        (pred_dir / "validation_metrics.json").write_text(
            json.dumps({"balanced_accuracy": 0.9}),
            encoding="utf-8",
        )
        (pred_dir / "test_metrics.json").write_text(
            json.dumps({"balanced_accuracy": 0.9, "metrics_source": "model_test"}),
            encoding="utf-8",
        )
        return True, [], []

    class _Cfg(SimpleNamespace):
        def with_backend_selection(self, _backend):
            return self

    monkeypatch.setattr(cli, "resolve_iteration_split", _fake_resolve_iteration_split)
    monkeypatch.setattr(cli, "generate_run_project", _fake_generate_run_project)
    monkeypatch.setattr(cli, "run_pipeline_for_iteration", _forbidden_run_pipeline)
    monkeypatch.setattr(cli, "run_pipeline_for_model", _fake_run_pipeline_for_model)
    monkeypatch.setattr(cli, "_write_model_mc_outputs", lambda **kwargs: None)
    monkeypatch.setattr(
        cli,
        "iteration_scalar_metrics_from_run_dir",
        lambda _run_dir: {"balanced_accuracy": 0.9, "metrics_source": "model_test"},
    )
    monkeypatch.setattr(
        cli,
        "load_project",
        lambda _path: SimpleNamespace(
            get_comparisons=lambda: [
                SimpleNamespace(control_group="healthy", disease_group="disease")
            ]
        ),
    )

    config = _Cfg(
        n_iterations=1,
        train_fraction=0.8,
        seed=42,
        samples_base_path=str(tmp_path),
        abort_on_step_failure=True,
    )
    backend_root = tmp_path / "model_mc" / "ecdf"
    cli._run_model_mc_backend(
        backend="ecdf",
        base_project_for_runs=tmp_path / "production_project.json",
        config=config,
        layout="binary",
        cohort_paths_list=[("healthy", ["h1", "h2", "h3"]), ("disease", ["d1", "d2", "d3"])],
        cohort_labels=["healthy", "disease"],
        control_paths=["h1", "h2", "h3"],
        disease_paths=["d1", "d2", "d3"],
        backend_root=backend_root,
        resume_arg=None,
        per_cancer_group=False,
        primary_monte_carlo_runs_root=primary_root,
    )

    linked_run = backend_root / "run_0001"
    assert linked_run.is_dir()
    assert (linked_run / "centroids").is_symlink()
    assert (linked_run / "detections").is_symlink()
    assert (linked_run / "centroids").resolve() == (run1 / "centroids").resolve()
    assert (linked_run / "detections").resolve() == (run1 / "detections").resolve()
    assert calls["generated_project"] == 1
    assert calls["ran_detector_stage"] == 0
    assert calls["ran_model_stage"] == 1
