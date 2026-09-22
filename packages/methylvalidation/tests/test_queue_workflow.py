"""Queue plan / export (no full pipeline)."""

from __future__ import annotations

import json
from pathlib import Path

from methyl_validation.queue_export import export_queue_artifacts


def _binary_project_for_mc(tmp_path: Path, monkeypatch) -> Path:
    h = tmp_path / "healthy.csv"
    d = tmp_path / "disease.csv"
    h.write_text("sample\nH1\nH2\nH3\nH4\nH5\n", encoding="utf-8")
    d.write_text("sample\nD1\nD2\nD3\nD4\nD5\n", encoding="utf-8")
    p = tmp_path / "project.json"
    p.write_text(
        json.dumps(
            {
                "project_name": "qtest",
                "output_base": str(tmp_path / "out"),
                "samples_base_path": str(tmp_path),
                "groups": [
                    {"label": "healthy", "sample_paths": [str(h)]},
                    {"label": "disease", "sample_paths": [str(d)]},
                ],
            }
        ),
        encoding="utf-8",
    )
    # Validation/MC knobs resolve from profile/site actionConfig (config-not-code).
    site = tmp_path / "methyl_site.json"
    site.write_text(
        json.dumps({"actionConfig": {"validation": {"train_fraction": 0.6, "n_iterations": 2}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("METHYL_SITE_CONFIG", str(site))
    return p


def test_export_queue_from_plan(tmp_path: Path, monkeypatch) -> None:
    from methyl_validation.mc_config_load import (
        apply_monte_carlo_config_overrides,
        ensure_monte_carlo_output_tree,
        load_monte_carlo_config,
    )
    from methyl_validation.planner import plan_discovery_runs
    from argparse import Namespace

    project = _binary_project_for_mc(tmp_path, monkeypatch)
    args = Namespace(
        project=project,
        config=None,
        iterations=None,
        seed=42,
        output_base=None,
        samples_base_path=None,
        path_remap=None,
        stability=None,
        stability_featurecuts=None,
        stability_target_ba=None,
        predictor_only=None,
        model_backend=None,
        post_model_backend=None,
        covariates_path=None,
        tabular_max_dmps=None,
        tabular_model_type=None,
        tabular_methods_json=None,
        tabular_save_train_dataset=None,
        tabular_train_dataset_path=None,
        generative_latent_dim=None,
        generative_kl_weight=None,
        generative_density_type=None,
        generative_epochs=None,
        generative_batch_size=None,
        generative_seed=None,
        generative_calibrate=None,
        no_generative_covariates_strict=None,
    )
    config, _ = load_monte_carlo_config(args, None)
    config = apply_monte_carlo_config_overrides(config, args)
    base, _bc, _ob, mcr = ensure_monte_carlo_output_tree(config)
    plan_discovery_runs(
        config=config, base_project=base, monte_carlo_runs_root=mcr, overwrite=False
    )
    summ = export_queue_artifacts(mcr)
    assert summ["n_tasks"] == 2
    man = mcr / "queue" / "queue_manifest.jsonl"
    assert man.is_file()
    line = man.read_text(encoding="utf-8").strip().splitlines()[0]
    rec = json.loads(line)
    assert "methyl-validation" in rec["command"]
    assert rec["command"][1] == "run-task"


def test_plan_runs_idempotent_replan_same_seed(tmp_path: Path, monkeypatch) -> None:
    import shutil

    from argparse import Namespace

    from methyl_validation.mc_config_load import (
        apply_monte_carlo_config_overrides,
        ensure_monte_carlo_output_tree,
        load_monte_carlo_config,
    )
    from methyl_validation.planner import plan_discovery_runs

    project = _binary_project_for_mc(tmp_path, monkeypatch)
    ns = dict(
        project=project,
        config=None,
        seed=99,
        path_remap=None,
        stability=None,
        stability_featurecuts=None,
        stability_target_ba=None,
        predictor_only=None,
        model_backend=None,
        post_model_backend=None,
        covariates_path=None,
        tabular_max_dmps=None,
        tabular_model_type=None,
        tabular_methods_json=None,
        tabular_save_train_dataset=None,
        tabular_train_dataset_path=None,
        generative_latent_dim=None,
        generative_kl_weight=None,
        generative_density_type=None,
        generative_epochs=None,
        generative_batch_size=None,
        generative_seed=None,
        generative_calibrate=None,
        no_generative_covariates_strict=None,
        iterations=None,
        output_base=None,
        samples_base_path=None,
    )
    args = Namespace(**ns)
    config, _ = load_monte_carlo_config(args, None)
    config = apply_monte_carlo_config_overrides(config, args)
    _, _, _, mcr = ensure_monte_carlo_output_tree(config)
    if mcr.is_dir():
        shutil.rmtree(mcr)
    plan_discovery_runs(
        config=config,
        base_project=Path(config.base_project),
        monte_carlo_runs_root=mcr,
        overwrite=True,
    )
    p1 = (mcr / "run_0001" / "project.json").read_text()
    shutil.rmtree(mcr)
    plan_discovery_runs(
        config=config,
        base_project=Path(config.base_project),
        monte_carlo_runs_root=mcr,
        overwrite=True,
    )
    p2 = (mcr / "run_0001" / "project.json").read_text()
    assert p1 == p2


def test_plan_runs_overwrite_does_not_delete_run_dirs(tmp_path: Path, monkeypatch) -> None:
    """--overwrite must refresh queue/plan without removing existing run_#### trees."""
    from argparse import Namespace

    from methyl_validation.mc_config_load import (
        apply_monte_carlo_config_overrides,
        ensure_monte_carlo_output_tree,
        load_monte_carlo_config,
    )
    from methyl_validation.planner import plan_discovery_runs

    project = _binary_project_for_mc(tmp_path, monkeypatch)
    ns = dict(
        project=project,
        config=None,
        seed=99,
        path_remap=None,
        stability=None,
        stability_featurecuts=None,
        stability_target_ba=None,
        predictor_only=None,
        model_backend=None,
        post_model_backend=None,
        covariates_path=None,
        tabular_max_dmps=None,
        tabular_model_type=None,
        tabular_methods_json=None,
        tabular_save_train_dataset=None,
        tabular_train_dataset_path=None,
        generative_latent_dim=None,
        generative_kl_weight=None,
        generative_density_type=None,
        generative_epochs=None,
        generative_batch_size=None,
        generative_seed=None,
        generative_calibrate=None,
        no_generative_covariates_strict=None,
        iterations=None,
        output_base=None,
        samples_base_path=None,
    )
    args = Namespace(**ns)
    config, _ = load_monte_carlo_config(args, None)
    config = apply_monte_carlo_config_overrides(config, args)
    *_, mcr = ensure_monte_carlo_output_tree(config)
    plan_discovery_runs(
        config=config,
        base_project=Path(config.base_project),
        monte_carlo_runs_root=mcr,
        overwrite=False,
    )
    marker = mcr / "run_0001" / "user_preserved_artifact.txt"
    marker.write_text("keep", encoding="utf-8")
    plan_discovery_runs(
        config=config,
        base_project=Path(config.base_project),
        monte_carlo_runs_root=mcr,
        overwrite=True,
        wipe_runs=False,
    )
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8") == "keep"


def test_plan_runs_wipe_runs_deletes_run_dirs(tmp_path: Path, monkeypatch) -> None:
    from argparse import Namespace

    from methyl_validation.mc_config_load import (
        apply_monte_carlo_config_overrides,
        ensure_monte_carlo_output_tree,
        load_monte_carlo_config,
    )
    from methyl_validation.planner import plan_discovery_runs

    project = _binary_project_for_mc(tmp_path, monkeypatch)
    ns = dict(
        project=project,
        config=None,
        seed=99,
        path_remap=None,
        stability=None,
        stability_featurecuts=None,
        stability_target_ba=None,
        predictor_only=None,
        model_backend=None,
        post_model_backend=None,
        covariates_path=None,
        tabular_max_dmps=None,
        tabular_model_type=None,
        tabular_methods_json=None,
        tabular_save_train_dataset=None,
        tabular_train_dataset_path=None,
        generative_latent_dim=None,
        generative_kl_weight=None,
        generative_density_type=None,
        generative_epochs=None,
        generative_batch_size=None,
        generative_seed=None,
        generative_calibrate=None,
        no_generative_covariates_strict=None,
        iterations=None,
        output_base=None,
        samples_base_path=None,
    )
    args = Namespace(**ns)
    config, _ = load_monte_carlo_config(args, None)
    config = apply_monte_carlo_config_overrides(config, args)
    *_, mcr = ensure_monte_carlo_output_tree(config)
    plan_discovery_runs(
        config=config,
        base_project=Path(config.base_project),
        monte_carlo_runs_root=mcr,
        overwrite=True,
    )
    marker = mcr / "run_0001" / "stale_artifact.txt"
    marker.write_text("gone", encoding="utf-8")
    plan_discovery_runs(
        config=config,
        base_project=Path(config.base_project),
        monte_carlo_runs_root=mcr,
        overwrite=True,
        wipe_runs=True,
    )
    assert not marker.exists()
    assert (mcr / "run_0001" / "project.json").is_file()


def test_plan_runs_incremental_preserves_completed_runs_and_adds_new(tmp_path: Path, monkeypatch) -> None:
    from argparse import Namespace

    from methyl_validation.mc_config_load import (
        apply_monte_carlo_config_overrides,
        ensure_monte_carlo_output_tree,
        load_monte_carlo_config,
    )
    from methyl_validation.planner import plan_discovery_runs
    from methyl_validation.storage_layout import atomic_write_json, run_status_path

    project = _binary_project_for_mc(tmp_path, monkeypatch)
    ns1 = _queue_ns_base(project, iterations=1)
    args = Namespace(**ns1)
    config, _ = load_monte_carlo_config(args, None)
    config = apply_monte_carlo_config_overrides(config, args)
    *_, mcr = ensure_monte_carlo_output_tree(config)
    if mcr.is_dir():
        import shutil

        shutil.rmtree(mcr)
    out1 = plan_discovery_runs(
        config=config,
        base_project=Path(config.base_project),
        monte_carlo_runs_root=mcr,
        overwrite=True,
    )
    assert int(out1["n_materialized"]) == 1
    p1 = (mcr / "run_0001" / "project.json").read_text(encoding="utf-8")
    atomic_write_json(
        run_status_path(mcr / "run_0001"),
        {"schema_version": "1.0", "status": "completed", "task_id": "t"},
    )
    ns2 = _queue_ns_base(project, iterations=2)
    args2 = Namespace(**ns2)
    config2, _ = load_monte_carlo_config(args2, None)
    config2 = apply_monte_carlo_config_overrides(config2, args2)
    out2 = plan_discovery_runs(
        config=config2,
        base_project=Path(config2.base_project),
        monte_carlo_runs_root=mcr,
        overwrite=False,
    )
    assert out2["n_preserved"] == 1
    assert out2["n_materialized"] == 1
    assert (mcr / "run_0001" / "project.json").read_text(encoding="utf-8") == p1
    assert (mcr / "run_0002" / "project.json").is_file()


def _queue_ns_base(project: Path, *, iterations: int) -> dict:
    return {
        "project": project,
        "config": None,
        "seed": 99,
        "iterations": iterations,
        "path_remap": None,
        "stability": None,
        "stability_featurecuts": None,
        "stability_target_ba": None,
        "predictor_only": None,
        "model_backend": None,
        "post_model_backend": None,
        "covariates_path": None,
        "tabular_max_dmps": None,
        "tabular_model_type": None,
        "tabular_methods_json": None,
        "tabular_save_train_dataset": None,
        "tabular_train_dataset_path": None,
        "generative_latent_dim": None,
        "generative_kl_weight": None,
        "generative_density_type": None,
        "generative_epochs": None,
        "generative_batch_size": None,
        "generative_seed": None,
        "generative_calibrate": None,
        "no_generative_covariates_strict": None,
        "output_base": None,
        "samples_base_path": None,
    }


def test_run_task_exits_0_if_already_completed(
    tmp_path: Path, monkeypatch,
) -> None:
    from argparse import Namespace

    from methyl_validation.executor import execute_discovery_task
    from methyl_validation.storage_layout import atomic_write_json, run_status_path

    from methyl_validation.mc_config_load import (
        apply_monte_carlo_config_overrides,
        ensure_monte_carlo_output_tree,
        load_monte_carlo_config,
    )
    from methyl_validation.planner import plan_discovery_runs

    project = _binary_project_for_mc(tmp_path, monkeypatch)
    args = Namespace(**_queue_ns_base(project, iterations=1))
    config, _ = load_monte_carlo_config(args, None)
    config = apply_monte_carlo_config_overrides(config, args)
    *_, mcr = ensure_monte_carlo_output_tree(config)
    if mcr.is_dir():
        import shutil

        shutil.rmtree(mcr)
    plan_discovery_runs(
        config=config,
        base_project=Path(config.base_project),
        monte_carlo_runs_root=mcr,
        overwrite=True,
    )
    tpath = mcr / "queue" / "tasks" / "run_0001.json"
    r1 = mcr / "run_0001"
    atomic_write_json(
        run_status_path(r1),
        {"schema_version": "1.0", "status": "completed", "task_id": "discovery_run_0001"},
    )
    from methyl_validation import pipeline_runner

    def boom(*_a, **_k):
        raise AssertionError("pipeline should not run when already completed")

    monkeypatch.setattr(pipeline_runner, "run_pipeline_for_iteration", boom)
    assert execute_discovery_task(str(tpath), force=False) == 0
    assert execute_discovery_task(str(tpath), force=True) == 1  # pipeline fails (assertion in stub)
