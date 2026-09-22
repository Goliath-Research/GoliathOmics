"""Tests for sample_prep_planner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from methyl_validation.sample_prep_planner import plan_sample_prep_context

S3_STORAGE = {
    "type": "s3",
    "bucket": "bucket",
    "region": "us-east-1",
    "credentials": {"authMode": "instance_profile"},
}

AZURE_STORAGE = {
    "type": "azure_blob",
    "account": "methylstore",
    "container": "plasma",
    "credentials": {"authMode": "default_credential"},
}

FILE_STORAGE = {
    "type": "file",
    "basePath": "/data/fastq",
}


def _write_project(tmp_path: Path, *, samples_base: str = "/work/samples") -> Path:
    project = {
        "project_name": "test_prep",
        "output_base": str(tmp_path / "out"),
        "samples_base_path": samples_base,
        "chromosomes": ["21"],
        "contexts": ["CG"],
        "controls": {
            "label": "healthy",
            "groups": [{"label": "all", "sample_paths": []}],
        },
        "diseases": {
            "label": "cancer",
            "groups": [{"label": "PCa", "sample_paths": []}],
        },
    }
    path = tmp_path / "project.json"
    path.write_text(json.dumps(project), encoding="utf-8")
    return path


def _write_csv(path: Path, rows: list[str]) -> None:
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _ref_fasta(tmp_path: Path) -> str:
    path = tmp_path / "genome.fa"
    if not path.is_file():
        path.write_text(">chr1\nACGT\n", encoding="utf-8")
    return str(path)


def _plan(tmp_path: Path, body: dict) -> dict:
    payload = dict(body)
    payload.setdefault("referenceFasta", _ref_fasta(tmp_path))
    return plan_sample_prep_context(payload)


def test_explicit_samples_with_s3_storage(tmp_path: Path) -> None:
    project_path = _write_project(tmp_path)
    ctx = _plan(
        tmp_path,
        {
            "projectPath": str(project_path),
            "samples": [{"sampleId": "S1"}, {"sampleId": "S2"}],
            "fastqStorage": S3_STORAGE,
        },
    )
    assert ctx["primaryAnalyte"] == "buffy_coat"
    assert ctx["isCfdna"] is False
    assert "deleteFastqs" not in ctx
    assert ctx["fastqStorage"]["type"] == "s3"
    assert len(ctx["samples"]) == 2
    s1 = ctx["samples"][0]
    assert s1["sampleId"] == "S1"
    assert s1["fastqPrefix"] == "S1/"
    assert s1["fastqSource"]["type"] == "s3"
    assert s1["fastqSource"]["prefix"] == "S1/"
    assert s1["fastqSource"]["bucket"] == "bucket"
    assert s1["sampleDir"].endswith("/S1")
    assert s1["sampleRoot"].endswith("/S1")
    assert s1["sampleDestination"] is None
    assert s1["h5Destination"] is None


def test_planner_keeps_explicit_arm_sample_dir(tmp_path: Path) -> None:
    project_path = _write_project(tmp_path)
    arm = tmp_path / "samples" / "S1" / "align.linear.mojo"
    ctx = _plan(
        tmp_path,
        {
            "projectPath": str(project_path),
            "samples": [{"sampleId": "S1", "sampleDir": str(arm)}],
            "fastqStorage": S3_STORAGE,
        },
    )
    assert ctx["samples"][0]["sampleDir"].endswith("align.linear.mojo")
    assert ctx["samples"][0]["sampleRoot"].endswith("/S1")


def test_planner_leaves_delete_fastqs_unset_for_profile_resolution(tmp_path: Path) -> None:
    """Unset planner field must not block actionConfig.sample_prep.delete_fastqs."""
    import sys
    from pathlib import Path as P

    domain = P(__file__).resolve().parents[3] / "workflow_engine" / "domain"
    if str(domain) not in sys.path:
        sys.path.insert(0, str(domain))
    from pipeline_profiles import seed_pipeline_scope_flags

    project_path = _write_project(tmp_path)
    ctx = _plan(
        tmp_path,
        {
            "projectPath": str(project_path),
            "samples": [{"sampleId": "S1"}],
            "fastqStorage": S3_STORAGE,
        }
    )
    assert "deleteFastqs" not in ctx
    seeded = seed_pipeline_scope_flags(
        ctx,
        action_config={"sample_prep": {"delete_fastqs": False}},
    )
    assert seeded["deleteFastqs"] is False

    forced = _plan(
        tmp_path,
        {
            "projectPath": str(project_path),
            "samples": [{"sampleId": "S1"}],
            "fastqStorage": S3_STORAGE,
            "deleteFastqs": True,
        }
    )
    assert forced["deleteFastqs"] is True
    seeded_forced = seed_pipeline_scope_flags(
        forced,
        action_config={"sample_prep": {"delete_fastqs": False}},
    )
    assert seeded_forced["deleteFastqs"] is True


def test_csv_plus_azure_storage(tmp_path: Path) -> None:
    samples_base = tmp_path / "samples"
    samples_base.mkdir()
    csv = tmp_path / "cohort.csv"
    _write_csv(csv, ["alpha", "beta"])
    project_path = _write_project(tmp_path, samples_base=str(samples_base))

    ctx = _plan(
        tmp_path,
        {
            "projectPath": str(project_path),
            "sampleCsv": str(csv),
            "fastqStorage": AZURE_STORAGE,
        }
    )
    assert len(ctx["samples"]) == 2
    ids = {s["sampleId"] for s in ctx["samples"]}
    assert ids == {"alpha", "beta"}
    assert ctx["samples"][0]["fastqSource"]["type"] == "azure_blob"
    assert ctx["samples"][0]["fastqSource"]["prefix"] == "alpha/"


def test_csv_rejects_legacy_uri(tmp_path: Path) -> None:
    samples_base = tmp_path / "samples"
    samples_base.mkdir()
    csv = tmp_path / "cohort.csv"
    _write_csv(csv, ["file:///data/fastq/S99/"])
    project_path = _write_project(tmp_path, samples_base=str(samples_base))

    with pytest.raises(ValueError, match="legacy FASTQ URI"):
        plan_sample_prep_context(
            {
                "projectPath": str(project_path),
                "sampleCsv": str(csv),
                "fastqStorage": FILE_STORAGE,
            }
        )


def test_use_project_samples(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    healthy = data / "healthy.csv"
    pca = data / "pca.csv"
    _write_csv(healthy, ["H1"])
    _write_csv(pca, ["P1"])

    project = {
        "project_name": "cohort",
        "output_base": str(tmp_path / "out"),
        "samples_base_path": str(tmp_path / "samples"),
        "chromosomes": ["21"],
        "controls": {
            "label": "healthy",
            "groups": [{"label": "all", "sample_paths": [str(healthy)]}],
        },
        "diseases": {
            "label": "cancer",
            "groups": [{"label": "PCa", "sample_paths": [str(pca)]}],
        },
    }
    project_path = tmp_path / "project.json"
    project_path.write_text(json.dumps(project), encoding="utf-8")

    ctx = _plan(
        tmp_path,
        {
            "projectPath": str(project_path),
            "useProjectSamples": True,
            "fastqStorage": S3_STORAGE,
        }
    )
    assert {s["sampleId"] for s in ctx["samples"]} == {"H1", "P1"}


def test_dedupe_by_sample_id(tmp_path: Path) -> None:
    project_path = _write_project(tmp_path)
    ctx = _plan(
        tmp_path,
        {
            "projectPath": str(project_path),
            "fastqStorage": S3_STORAGE,
            "samples": [
                {"sampleId": "S1", "fastqPrefix": "x/S1/"},
                {"sampleId": "S1", "fastqPrefix": "y/S1/"},
            ],
        }
    )
    assert len(ctx["samples"]) == 1


def test_explicit_fastq_source_override(tmp_path: Path) -> None:
    project_path = _write_project(tmp_path)
    override = {
        "type": "file",
        "basePath": "/mnt/custom",
        "prefix": "lane1/S1/",
    }
    ctx = _plan(
        tmp_path,
        {
            "projectPath": str(project_path),
            "fastqStorage": FILE_STORAGE,
            "samples": [{"sampleId": "S1", "fastqSource": override}],
        }
    )
    assert ctx["samples"][0]["fastqSource"] == override


def test_cfdna_primary_analyte(tmp_path: Path) -> None:
    project = {
        "project_name": "plasma",
        "output_base": str(tmp_path / "out"),
        "samples_base_path": "/work/samples",
        "chromosomes": ["21"],
        "controls": {
            "label": "healthy",
            "groups": [{"label": "all", "sample_paths": []}],
        },
        "diseases": {
            "label": "cancer",
            "groups": [{"label": "PCa", "sample_paths": []}],
        },
        "regulatory": {"primary_analyte": "cfdna"},
    }
    project_path = tmp_path / "project.json"
    project_path.write_text(json.dumps(project), encoding="utf-8")

    ctx = _plan(
        tmp_path,
        {
            "projectPath": str(project_path),
            "samples": [{"sampleId": "S1"}],
            "fastqStorage": S3_STORAGE,
        }
    )
    assert ctx["primaryAnalyte"] == "cfdna"
    assert ctx["isCfdna"] is True


def test_project_analyte_overrides_request_cfdna(tmp_path: Path) -> None:
    project_path = _write_project(tmp_path)
    data = json.loads(project_path.read_text(encoding="utf-8"))
    data["regulatory"] = {"primary_analyte": "buffy_coat"}
    project_path.write_text(json.dumps(data), encoding="utf-8")
    ctx = _plan(
        tmp_path,
        {
            "projectPath": str(project_path),
            "samples": [{"sampleId": "S1"}],
            "fastqStorage": S3_STORAGE,
            "primaryAnalyte": "cfdna",
        },
    )
    assert ctx["primaryAnalyte"] == "buffy_coat"
    assert ctx["isCfdna"] is False


@pytest.mark.parametrize("analyte", ["plasma", "cell_free_dna", "Plasma", "CELL-FREE-DNA"])
def test_planner_is_cfdna_for_plasma_aliases(tmp_path: Path, analyte: str) -> None:
    project_path = _write_project(tmp_path)
    ctx = _plan(
        tmp_path,
        {
            "projectPath": str(project_path),
            "samples": [{"sampleId": "S1"}],
            "fastqStorage": S3_STORAGE,
            "primaryAnalyte": analyte,
        },
    )
    assert ctx["isCfdna"] is True
    assert ctx["primaryAnalyte"] == analyte.strip().lower().replace("-", "_")


H5_STORAGE = {
    "type": "s3",
    "bucket": "methyl-archive",
    "region": "us-east-1",
    "prefixBase": "archive/",
    "credentials": {"authMode": "instance_profile"},
}


def test_h5_storage_materializes_per_sample_destination(tmp_path: Path) -> None:
    project_path = _write_project(tmp_path)
    ctx = _plan(
        tmp_path,
        {
            "projectPath": str(project_path),
            "samples": [{"sampleId": "S1", "fastqPrefix": "cohort/S1/"}],
            "fastqStorage": S3_STORAGE,
            "h5Storage": H5_STORAGE,
        }
    )
    assert ctx["h5Storage"]["bucket"] == "methyl-archive"
    s1 = ctx["samples"][0]
    assert s1["sampleDestination"]["type"] == "s3"
    assert s1["sampleDestination"]["bucket"] == "methyl-archive"
    assert s1["sampleDestination"]["prefix"] == "archive/S1/"
    assert s1["h5Destination"]["prefix"] == "archive/S1/"


def test_prefix_base_on_fastq_storage(tmp_path: Path) -> None:
    project_path = _write_project(tmp_path)
    storage = {
        "type": "s3",
        "bucket": "goliath",
        "region": "us-east-1",
        "endpointUrl": "https://s3.us-east-1.myqnapcloud.io",
        "prefixBase": "samples/",
        "credentials": {"authMode": "instance_profile"},
    }
    ctx = _plan(
        tmp_path,
        {
            "projectPath": str(project_path),
            "samples": [{"sampleId": "S1"}],
            "fastqStorage": storage,
        }
    )
    assert ctx["samples"][0]["fastqPrefix"] == "samples/S1/"
    assert ctx["samples"][0]["fastqSource"]["prefix"] == "samples/S1/"
    assert ctx["samples"][0]["sampleDestination"] is None
