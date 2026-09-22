from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from methyl_validation.mc_manifest import (
    CLASSIFIER_EXTENDED_DMP_CSV_PATTERN,
    DISCOVERY_DMP_CSV_PATTERN,
    write_mapper_classifier_override,
)
from methyl_validation.stability import load_classifier_dmp_panel


def test_write_mapper_classifier_override(tmp_path: Path):
    payload = write_mapper_classifier_override(tmp_path / "run_0001")
    assert CLASSIFIER_EXTENDED_DMP_CSV_PATTERN in payload["csv_filename_pattern"] or DISCOVERY_DMP_CSV_PATTERN in payload["csv_filename_pattern"]
    assert payload["enrich_disease"] is False


def test_write_mapper_classifier_override_honors_enrich_disease_flag(tmp_path: Path):
    class _Cfg:
        stability_mapper_enrich_disease = True

    payload = write_mapper_classifier_override(tmp_path / "run_0001", _Cfg())
    assert payload["enrich_disease"] is True


def test_load_classifier_dmp_panel_dedupes_and_caps(tmp_path: Path):
    run_dir = tmp_path / "run_0001"
    det = run_dir / "detections" / "all" / "cmp"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1", "1", "2"],
            "position": [100, 100, 200],
            "context": ["CG", "CG", "CG"],
            "effect_size": [0.2, 0.9, 0.5],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)
    pd.DataFrame(
        {
            "chromosome": ["1"],
            "position": [300],
            "context": ["CG"],
            "effect_size": [0.4],
        }
    ).to_csv(det / "dmps-2-classifier.csv", index=False)
    pd.DataFrame(
        {
            "chromosome": ["1", "1", "2"],
            "position": [100, 300, 200],
            "context": ["CG", "CG", "CG"],
            "effect_size": [0.9, 0.4, 0.5],
        }
    ).to_csv(det / "dmps-1-classifier-extended.csv", index=False)
    pd.DataFrame(
        {
            "chromosome": ["2"],
            "position": [200],
            "context": ["CG"],
            "effect_size": [0.5],
        }
    ).to_csv(det / "dmps-2-classifier-extended.csv", index=False)

    full = load_classifier_dmp_panel(run_dir)
    assert full is not None
    assert len(full) == 3

    capped = load_classifier_dmp_panel(run_dir, max_dmps=1)
    assert capped is not None
    assert len(capped) == 1
    assert int(capped.iloc[0]["position"]) == 100
    assert float(capped.iloc[0]["effect_size"]) == pytest.approx(0.9)
