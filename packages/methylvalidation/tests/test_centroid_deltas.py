import json
from pathlib import Path
from tempfile import TemporaryDirectory

from methyl_validation import pipeline_runner
from methyl_validation.pipeline_runner import run_centroid
from methyl_validation.project_gen import (
    carry_forward_centroids_from_previous_run,
    centroid_override_has_remove_samples,
    generate_run_project,
    incremental_centroid_update_requested,
    prepare_incremental_centroid_baseline,
)


def write_base_project(path: Path) -> None:
    payload = {
        "project_name": "validation-project",
        "controls": {
            "label": "healthy",
            "groups": [{"label": "healthy", "sample_paths": ["ignored.csv"]}],
        },
        "diseases": {
            "label": "disease",
            "groups": [{"label": "disease", "sample_paths": ["ignored.csv"]}],
        },
        "step_config": {
            "predictor": {
                "controls": {
                    "label": "healthy",
                    "groups": [{"label": "healthy", "sample_paths": ["configs/wrong_control.csv"]}],
                },
                "diseases": {
                    "label": "disease",
                    "groups": [{"label": "disease", "sample_paths": ["configs/wrong_disease.csv"]}],
                },
            }
        },
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_generate_run_project_writes_group_specific_centroid_deltas():
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        base_project = root / "base_project.json"
        run_dir = root / "run_0001"
        write_base_project(base_project)

        current_control = ["/samples/control_b", "/samples/control_c"]
        current_disease = ["/samples/disease_y", "/samples/disease_z"]
        previous_control = ["/samples/control_a", "/samples/control_b"]
        previous_disease = ["/samples/disease_x", "/samples/disease_y"]

        (
            project_path,
            _train_control_csv,
            _train_disease_csv,
            _val_control_csv,
            _val_disease_csv,
            group1_override,
            group2_override,
        ) = generate_run_project(
            base_project,
            run_dir,
            "run_0001",
            str(root / "runs"),
            current_control,
            current_disease,
            ["/samples/val_control"],
            ["/samples/val_disease"],
            "/samples",
            previous_train_control_paths=previous_control,
            previous_train_disease_paths=previous_disease,
        )

        assert project_path.exists()
        assert group1_override.exists()
        assert group2_override.exists()

        group1_payload = json.loads(group1_override.read_text(encoding="utf-8"))
        assert "samples" not in group1_payload["base_config"]
        assert group1_payload["base_config"]["add_samples"] == ["/samples/control_c"]
        assert group1_payload["base_config"]["remove_samples"] == ["/samples/control_a"]

        group2_payload = json.loads(group2_override.read_text(encoding="utf-8"))
        assert "samples" not in group2_payload["base_config"]
        assert group2_payload["base_config"]["add_samples"] == ["/samples/disease_z"]
        assert group2_payload["base_config"]["remove_samples"] == ["/samples/disease_x"]

        run_proj = json.loads(project_path.read_text(encoding="utf-8"))
        assert run_proj["controls"]["label"] == "healthy"
        assert run_proj["controls"]["groups"][0]["label"] == "healthy"
        assert run_proj["diseases"]["label"] == "disease"
        assert "step_config" not in run_proj
        val_groups = json.loads((run_dir / "test_groups.json").read_text(encoding="utf-8"))
        assert len(val_groups) == 2
        assert val_groups[0]["paths"] == [str(Path("/samples/val_control").resolve())]
        assert val_groups[1]["paths"] == [str(Path("/samples/val_disease").resolve())]


def test_generate_run_project_creates_predictor_holdouts_when_missing():
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        base_project = root / "base_project.json"
        run_dir = root / "run_0002"
        payload = {
            "project_name": "validation-project",
            "controls": {
                "label": "healthy",
                "groups": [{"label": "all", "sample_paths": ["ignored.csv"]}],
            },
            "diseases": {
                "label": "cancer",
                "groups": [{"label": "PCa", "sample_paths": ["ignored.csv"]}],
            },
            "step_config": {"detection": {"alpha": 0.05}},
        }
        base_project.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        project_path, _, _, val_control_csv, val_disease_csv, _, _ = generate_run_project(
            base_project,
            run_dir,
            "run_0002",
            str(root / "runs"),
            ["/samples/control_a"],
            ["/samples/disease_a"],
            ["/samples/val_control"],
            ["/samples/val_disease"],
            "/samples",
        )

        run_proj = json.loads(project_path.read_text(encoding="utf-8"))
        assert run_proj["controls"]["label"] == "healthy"
        assert run_proj["controls"]["groups"][0]["label"] == "all"
        assert run_proj["diseases"]["label"] == "cancer"
        assert run_proj["diseases"]["groups"][0]["label"] == "PCa"
        assert "step_config" not in run_proj
        val_groups = json.loads((run_dir / "test_groups.json").read_text(encoding="utf-8"))
        assert val_groups[0]["paths"] == [str(Path("/samples/val_control").resolve())]
        assert val_groups[1]["paths"] == [str(Path("/samples/val_disease").resolve())]
        assert val_control_csv.is_file()
        assert val_disease_csv.is_file()


def test_run_centroid_executes_group_specific_step_overrides(monkeypatch):
    calls = []

    def fake_run_cmd(cmd, cwd=None, env=None):
        calls.append(cmd)
        return 0, "ok", ""

    monkeypatch.setattr("methyl_validation.pipeline_runner.run_cmd", fake_run_cmd)

    rc, stdout, stderr = run_centroid(
        "/tmp/project.json",
        centroid_step_overrides={
            "group1": "/tmp/group1.json",
            "group2": "/tmp/group2.json",
        },
    )

    assert rc == 0
    assert "group1 stdout" in stdout
    assert stderr == "=== group1 stderr ===\n\n=== group2 stderr ===\n"
    assert calls == [
        [
            "methyl-centroid",
            "--project",
            "/tmp/project.json",
            "--group",
            "group1",
            "--step-override",
            "/tmp/group1.json",
        ],
        [
            "methyl-centroid",
            "--project",
            "/tmp/project.json",
            "--group",
            "group2",
            "--step-override",
            "/tmp/group2.json",
        ],
    ]


def test_run_pipeline_for_iteration_splits_centroid_logs_and_counts(monkeypatch):
    calls = []

    def fake_run_centroid_group(project_json, group, step_override=None):
        calls.append(("centroid", str(group), str(step_override) if step_override is not None else None))
        return 0, f"{group}-stdout", ""

    def fake_run_detector(project_json, per_cancer_group=False, detector_step_override=None):
        calls.append(("detector", per_cancer_group, str(detector_step_override) if detector_step_override is not None else None))
        return 0, "detector-stdout", ""

    def fake_processed_samples(project_json, group, step_override=None):
        return 11 if str(group) == "group1" else 9

    monkeypatch.setattr(pipeline_runner, "run_centroid_group", fake_run_centroid_group)
    monkeypatch.setattr(pipeline_runner, "run_detector", fake_run_detector)
    monkeypatch.setattr(
        pipeline_runner,
        "_read_centroid_processed_samples",
        fake_processed_samples,
    )

    with TemporaryDirectory() as temp_dir:
        logs_dir = Path(temp_dir) / "run_0001" / "logs"
        ok, errors, timings = pipeline_runner.run_pipeline_for_iteration(
            Path("/tmp/project.json"),
            per_cancer_group=False,
            logs_dir=logs_dir,
            centroid_step_overrides={
                "group1": Path("/tmp/group1.json"),
                "group2": Path("/tmp/group2.json"),
            },
        )

        assert ok
        assert errors == []
        assert [t["step_name"] for t in timings] == [
            "methyl-centroid-group1",
            "methyl-centroid-group2",
            "methyl-detector",
        ]
        assert timings[0]["n_processed_samples"] == 11
        assert timings[1]["n_processed_samples"] == 9
        assert "n_processed_samples" not in timings[2]

        assert (logs_dir / "methyl-centroid-group1.log").is_file()
        assert (logs_dir / "methyl-centroid-group2.log").is_file()
        assert (logs_dir / "methyl-detector.log").is_file()


def test_carry_forward_centroids_copies_tree_for_incremental_baseline():
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prev = root / "run_0001"
        cur = root / "run_0002"
        (prev / "centroids" / "controls" / "all" / "all").mkdir(parents=True)
        marker = prev / "centroids" / "controls" / "all" / "all" / "1-CG.h5"
        marker.write_bytes(b"centroid")

        assert carry_forward_centroids_from_previous_run(prev, cur)
        assert (cur / "centroids" / "controls" / "all" / "all" / "1-CG.h5").is_file()


def test_prepare_incremental_centroid_baseline_skips_without_remove_samples():
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prev = root / "run_0001"
        cur = root / "run_0002"
        (prev / "centroids").mkdir(parents=True)
        override = root / "centroid_group1_override.json"
        override.write_text(
            json.dumps({"base_config": {"add_samples": ["/samples/a"], "remove_samples": []}}),
            encoding="utf-8",
        )
        assert not prepare_incremental_centroid_baseline(prev, cur, override, None)
        assert not (cur / "centroids").exists()


def test_incremental_centroid_update_requested_when_remove_present():
    with TemporaryDirectory() as temp_dir:
        override = Path(temp_dir) / "override.json"
        override.write_text(
            json.dumps({"base_config": {"add_samples": ["/a"], "remove_samples": ["/b"]}}),
            encoding="utf-8",
        )
        assert centroid_override_has_remove_samples(override)
        assert incremental_centroid_update_requested(override, None)


def test_run_pipeline_for_iteration_prepares_centroid_baseline(monkeypatch):
    prepared = []

    def fake_prepare(previous_run_dir, current_run_dir, c1, c2):
        prepared.append((str(previous_run_dir), str(current_run_dir)))
        return True

    def fake_run_centroid_group(project_json, group, step_override=None):
        return 0, "ok", ""

    def fake_run_detector(project_json, per_cancer_group=False, detector_step_override=None):
        return 0, "ok", ""

    monkeypatch.setattr(
        "methyl_validation.project_gen.prepare_incremental_centroid_baseline",
        fake_prepare,
    )
    monkeypatch.setattr(pipeline_runner, "run_centroid_group", fake_run_centroid_group)
    monkeypatch.setattr(pipeline_runner, "run_detector", fake_run_detector)

    with TemporaryDirectory() as temp_dir:
        run_dir = Path(temp_dir) / "run_0002"
        run_dir.mkdir(parents=True)
        project_json = run_dir / "project.json"
        project_json.write_text("{}", encoding="utf-8")
        ok, errors, _ = pipeline_runner.run_pipeline_for_iteration(
            project_json,
            centroid_step_overrides={
                "group1": Path(temp_dir) / "g1.json",
                "group2": Path(temp_dir) / "g2.json",
            },
        )
        assert ok
        assert errors == []
        assert len(prepared) == 1
        assert prepared[0][0].endswith("run_0001")
        assert prepared[0][1].endswith("run_0002")
