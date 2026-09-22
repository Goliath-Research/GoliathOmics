"""MethylQcTaskInput is extra=forbid; alignmentMode is a typed field, not getattr."""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from methyl_worker.action_catalog import find_catalog_entry
from methyl_worker.action_execution import validate_input
from methyl_worker.handlers import sample_prep as sample_prep_handlers
from methyl_worker.task_models.sample_prep_models import MethylQcTaskInput, MethylQcTaskOutput


def test_alignment_mode_is_declared_on_methyl_qc_input() -> None:
    assert "alignmentMode" in MethylQcTaskInput.model_fields
    assert MethylQcTaskInput.model_config.get("extra") == "forbid"


def test_methyl_qc_input_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra"):
        MethylQcTaskInput.model_validate(
            {
                "tool": "MethylAlignmentQc",
                "sampleId": "S1",
                "sampleDir": "/work/samples/S1",
                "notAField": True,
            }
        )


def test_validate_input_keeps_alignment_mode() -> None:
    entry = find_catalog_entry("sample.methyl_qc")
    assert entry is not None
    model = validate_input(
        entry,
        {
            "tool": "MethylAlignmentQc",
            "sampleId": "S1",
            "sampleDir": "/work/samples/S1",
            "alignmentMode": "linear",
        },
    )
    assert isinstance(model, MethylQcTaskInput)
    assert model.alignmentMode == "linear"


def test_qc_handler_reads_typed_alignment_mode() -> None:
    src = inspect.getsource(sample_prep_handlers._handle_methyl_qc)
    assert "getattr(input, \"alignmentMode\"" not in src
    assert "input.alignmentMode" in src
    assert 'resolvedConfig or {}).get("alignmentMode")' not in src


def test_methyl_qc_task_io_is_not_export_payload() -> None:
    from methyl_alignment_qc.models.sample_qc_v2 import PICARD_TABLE_KEYS, ExportedSampleQCV2Payload

    input_fields = set(MethylQcTaskInput.model_fields)
    output_fields = set(MethylQcTaskOutput.model_fields)
    export_fields = set(ExportedSampleQCV2Payload.model_fields)
    for key in PICARD_TABLE_KEYS:
        assert key not in input_fields
        assert key not in output_fields
        assert key not in export_fields
    assert "sampleDir" in input_fields
    assert "qcPath" in output_fields
    assert "metadata" not in input_fields
    assert "mean_quality_by_cycle" not in input_fields
    with pytest.raises(ValidationError):
        MethylQcTaskInput.model_validate(
            {
                "sample_id": "S1",
                "metadata": {"schema_version": "2.1.0", "export_kind": "guardrail_summary"},
                "summary_stats": {},
                "guardrails": {},
                "duplication_metrics": [],
            }
        )
