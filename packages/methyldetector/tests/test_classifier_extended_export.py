from __future__ import annotations

from pathlib import Path

import pandas as pd

from methyl_detector.core.methyldetector import MethylDetector
from methyl_detector.models.config import MethylDetectorConfig


def _build_detector(tmp_path: Path, **kwargs) -> MethylDetector:
    c1 = tmp_path / "c1"
    c2 = tmp_path / "c2"
    c1.mkdir()
    c2.mkdir()
    cfg = MethylDetectorConfig.model_validate(
        {
            "chromosome": "1",
            "contexts": ["CG"],
            "centroid1_dir": str(c1),
            "centroid2_dir": str(c2),
            "output_dir": str(tmp_path / "out"),
            **kwargs,
        }
    )
    return MethylDetector(cfg)


def test_compute_extended_panel_k_applies_margin_and_cap(tmp_path: Path):
    pct_detector = _build_detector(
        tmp_path,
        classifier_export_margin_pct=0.10,
        classifier_export_margin_abs=0,
        classifier_export_max_dmps=150,
    )
    assert pct_detector._compute_extended_panel_k(100, 1000) == 110

    capped_root = tmp_path / "capped"
    capped_root.mkdir()
    capped_detector = _build_detector(
        capped_root,
        classifier_export_margin_pct=0.10,
        classifier_export_margin_abs=0,
        classifier_export_max_dmps=150,
    )
    assert capped_detector._compute_extended_panel_k(140, 1000) == 150
    assert capped_detector._compute_extended_panel_k(10, 12) == 11


def test_classifier_extended_dmps_from_core_expands_ranked_pool(tmp_path: Path):
    detector = _build_detector(
        tmp_path,
        classifier_export_margin_pct=0.0,
        classifier_export_margin_abs=2,
        classifier_export_max_dmps=200,
    )
    sorted_df = pd.DataFrame(
        {
            "position": [100, 200, 300, 400],
            "effect_size": [0.9, 0.7, 0.5, 0.3],
        }
    )
    core = sorted_df.iloc[:2].copy()
    extended = detector._classifier_extended_dmps_from_core(sorted_df, core)
    assert len(extended) == 4
    assert list(extended["position"]) == [100, 200, 300, 400]
