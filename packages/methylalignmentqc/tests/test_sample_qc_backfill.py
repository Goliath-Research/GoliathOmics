"""Integration tests for retroactive sample QC backfill (V2 export + guardrails)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from methyl_alignment_qc.core.writer import build_sample_qc_v2_dict
from methyl_alignment_qc.models.config import (
    AlignmentGuardrailsConfig,
    BisulfiteConversionConfig,
    CycleScreeningConfig,
    FragmentomicsConfig,
)
from methyl_alignment_qc.models.sample_qc_v2 import ExportedSampleQCV2Payload
from test_writer_guardrails import (
    _dedup_metrics_fixture,
    _parabricks_json_fixture,
    _write_text,
)

FIXTURE_SAMPLE = Path(__file__).resolve().parents[1] / "data" / "003772_8C9_3"


def _write_minimal_sample_dir(tmp_path: Path, sample_name: str = "sampleA") -> Path:
    sample_dir = tmp_path / sample_name
    sample_dir.mkdir(parents=True)
    _write_text(sample_dir / f"{sample_name}.deduplicate_metrics.txt", _dedup_metrics_fixture())
    (sample_dir / f"{sample_name}.json").write_text(
        json.dumps(_parabricks_json_fixture(sample_name)),
        encoding="utf-8",
    )
    return sample_dir


def test_build_sample_qc_v2_dict_minimal_guardrails(tmp_path: Path) -> None:
    sample_dir = _write_minimal_sample_dir(tmp_path)
    payload = build_sample_qc_v2_dict(
        sample_dir,
        cycle_screening=CycleScreeningConfig(),
    )
    ExportedSampleQCV2Payload.model_validate(payload)
    assert payload["sample_id"] == "sampleA"
    assert payload["metadata"]["schema_version"].startswith("2.")
    assert "guardrails" in payload
    assert "details" in payload["guardrails"]
    assert "pf_percent" in payload["guardrails"]["details"]


def test_build_sample_qc_v2_dict_cfdna_profile_layers(tmp_path: Path) -> None:
    sample_dir = _write_minimal_sample_dir(tmp_path, "sample_cf")
    payload = build_sample_qc_v2_dict(
        sample_dir,
        fragmentomics=FragmentomicsConfig(enabled=True, profile="cfdna"),
        bisulfite_conversion=BisulfiteConversionConfig(enabled=True, source="auto"),
        alignment_guardrails=AlignmentGuardrailsConfig(enabled=True, flagstat_enabled=True),
        cycle_screening=CycleScreeningConfig(),
    )
    ExportedSampleQCV2Payload.model_validate(payload)
    assert payload.get("alignment_stats") is not None
    assert "mapping_rate" in payload["guardrails"]["details"]
    assert payload.get("fragmentomics_metrics") is not None
    assert payload.get("bisulfite_conversion_metrics") is not None
    assert "deamination_qscore" not in payload["bisulfite_conversion_metrics"]
    details = payload["guardrails"]["details"]
    assert "deamination_qscore" in details
    bis = details.get("bisulfite_conversion") or {}
    assert "deamination_qscore" not in bis
    # No BAM in fixture: flagstat guardrails should record failure, not crash
    assert "properly_paired_rate" in payload["guardrails"]["details"]


@pytest.mark.skipif(not FIXTURE_SAMPLE.is_dir(), reason="package fixture sample missing")
def test_build_sample_qc_v2_dict_real_fixture_sample() -> None:
    payload = build_sample_qc_v2_dict(
        FIXTURE_SAMPLE,
        alignment_guardrails=AlignmentGuardrailsConfig(enabled=True, flagstat_enabled=True),
        cycle_screening=CycleScreeningConfig(),
    )
    ExportedSampleQCV2Payload.model_validate(payload)
    assert payload["sample_id"] == "003772_8C9_3"
    assert payload.get("alignment_stats") is not None
    assert float(payload["alignment_stats"]["mapping_rate"]) > 0.9


def test_backfill_cli_writes_json_and_summary(tmp_path: Path) -> None:
    sample_dir = _write_minimal_sample_dir(tmp_path)
    out = tmp_path / "out" / "sampleA.sample_qc.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "methyl_alignment_qc.cli.sample_qc_backfill",
            str(sample_dir),
            "--analyte",
            "cfdna",
            "-o",
            str(out),
            "--quiet-summary",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert out.is_file(), proc.stderr or proc.stdout
    payload = json.loads(out.read_text(encoding="utf-8"))
    ExportedSampleQCV2Payload.model_validate(payload)
    assert payload["sample_id"] == "sampleA"
    assert payload.get("fragmentomics_metrics") is not None


def test_backfill_cli_stdout_and_output_both_written(tmp_path: Path) -> None:
    sample_dir = _write_minimal_sample_dir(tmp_path)
    out = tmp_path / "dual.json"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "methyl_alignment_qc.cli.sample_qc_backfill",
            str(sample_dir),
            "-o",
            str(out),
            "--stdout",
            "--quiet-summary",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.is_file()
    file_payload = json.loads(out.read_text(encoding="utf-8"))
    stdout_payload = json.loads(proc.stdout)
    assert file_payload["sample_id"] == "sampleA"
    assert stdout_payload["sample_id"] == "sampleA"


def test_process_samples_to_qc_jsons_parses_dedup_metrics_once(tmp_path: Path, monkeypatch) -> None:
    from methyl_alignment_qc.core import parser as core_parser
    from methyl_alignment_qc.core.writer import process_samples_to_qc_jsons

    sample_a = _write_minimal_sample_dir(tmp_path, "sampleA")
    sample_b = _write_minimal_sample_dir(tmp_path, "sampleB")
    calls = {"n": 0}
    real_parse = core_parser.parse_metrics_from_sample_paths

    def counting_parse(paths, **kwargs):
        calls["n"] += 1
        return real_parse(paths, **kwargs)

    monkeypatch.setattr(core_parser, "parse_metrics_from_sample_paths", counting_parse)

    output_dir = tmp_path / "out"
    process_samples_to_qc_jsons([str(sample_a), str(sample_b)], str(output_dir), validate_schema=True)
    assert calls["n"] == 1
    assert (output_dir / "sampleA.json").is_file()
    assert (output_dir / "sampleB.json").is_file()
