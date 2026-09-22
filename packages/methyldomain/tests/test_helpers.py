"""Unit tests for methyl_domain helper builders and tagged-JSON round trips.

These cover the frequently-imported pure helpers that do not require /work
study manifests (see test_resolved_project.py for the manifest-backed path).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from methyl_domain.helpers import (
    MethylDetectionRef_from_detector_output,
    _csv_sample_count,
    _safe_cohort_filename_label,
    apply_qc_to_sample,
    comparisons_from_project_json,
    resolve_methylation_h5_path,
)
from methyl_domain.types import (
    MethylationMatrixRef,
    MethylSampleRef,
    parse_domain_value,
    to_tagged_json,
)


def test_safe_cohort_filename_label_sanitizes_and_falls_back() -> None:
    assert _safe_cohort_filename_label("Healthy Controls!") == "Healthy_Controls_"
    assert _safe_cohort_filename_label("PCa-1.2") == "PCa-1.2"
    assert _safe_cohort_filename_label("   ") == "cohort"


def test_csv_sample_count_excludes_header(tmp_path: Path) -> None:
    csv = tmp_path / "train.csv"
    csv.write_text("path\n/a\n/b\n/c\n", encoding="utf-8")
    assert _csv_sample_count(csv) == 3
    assert _csv_sample_count(tmp_path / "missing.csv") == 0


def test_detection_ref_accepts_both_key_casings() -> None:
    camel = MethylDetectionRef_from_detector_output(
        {"nDmps": "42", "dmpCsvPath": "/out/dmps.csv"},
        comparison_label="H_vs_PCa",
        control_group="Healthy",
        disease_group="PCa",
    )
    snake = MethylDetectionRef_from_detector_output(
        {"n_dmps": 42, "dmp_csv_path": "/out/dmps.csv"},
        comparison_label="H_vs_PCa",
        control_group="Healthy",
        disease_group="PCa",
    )
    assert camel.nDmps == 42
    assert snake.nDmps == 42
    assert camel.dmpCsvPath == "/out/dmps.csv"


def test_resolve_methylation_h5_path_prefers_listed_files() -> None:
    sample = MethylSampleRef(
        sampleId="s1",
        sampleDir="/data/s1",
        methylation=MethylationMatrixRef(
            sampleDir="/data/s1",
            chromosomes=["1"],
            contexts=["CG"],
            h5Files=["/other/1-CG.h5"],
        ),
    )
    resolved = resolve_methylation_h5_path(sample, chromosome="1", context="CG")
    assert resolved == Path("/data/s1/1-CG.h5")


def test_resolve_methylation_h5_path_uses_pattern_when_no_files() -> None:
    sample = MethylSampleRef(
        sampleId="s1",
        sampleDir="/data/s1",
        methylation=MethylationMatrixRef(sampleDir="/data/s1"),
    )
    resolved = resolve_methylation_h5_path(sample, chromosome="X", context="CHG")
    assert resolved == Path("/data/s1/X-CHG.h5")


def test_resolve_methylation_h5_path_requires_methylation_ref() -> None:
    sample = MethylSampleRef(sampleId="s1", sampleDir="/data/s1")
    with pytest.raises(ValueError, match="no methylation ref"):
        resolve_methylation_h5_path(sample, chromosome="1")


def test_comparisons_from_project_json_reads_both_casings(tmp_path: Path) -> None:
    project = tmp_path / "project.json"
    project.write_text(
        json.dumps(
            {
                "comparisons": [
                    {"control_group": "Healthy", "disease_group": "PCa", "label": "H_vs_PCa"},
                    {"controlGroup": "Healthy", "diseaseGroup": "BPH"},
                    {"disease_group": "OnlyDisease"},  # missing control -> skipped
                ]
            }
        ),
        encoding="utf-8",
    )
    comparisons = comparisons_from_project_json(project)
    assert [c.diseaseGroup for c in comparisons] == ["PCa", "BPH"]
    assert comparisons[0].comparisonLabel == "H_vs_PCa"


def test_apply_qc_and_tagged_json_roundtrip() -> None:
    sample = MethylSampleRef(sampleId="s1", sampleDir="/data/s1")
    updated = apply_qc_to_sample(sample, qc_path="/qc/s1.json", overall_pass=True)
    assert updated.alignmentQc is not None
    assert updated.alignmentQc.overallPass is True

    payload = to_tagged_json(updated)
    assert payload["$type"] == "MethylSampleRef"

    reparsed = parse_domain_value(payload)
    assert isinstance(reparsed, MethylSampleRef)
    assert reparsed.alignmentQc.qcPath == "/qc/s1.json"


def test_parse_domain_value_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="unknown domain"):
        parse_domain_value({"$type": "NotARealType"})
