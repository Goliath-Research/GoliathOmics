from __future__ import annotations

from pathlib import Path

import pandas as pd

from methyl_gene_select.core.dmp_panel import load_classifier_dmp_panel, load_discovery_dmp_panel


def _write_detection_csvs(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run_0001"
    det = run_dir / "detections" / "all" / "PCa"
    det.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1", "1", "2"],
            "position": [100, 200, 300],
            "context": ["CG", "CG", "CG"],
            "effect_size": [0.9, 0.5, 0.7],
        }
    ).to_csv(det / "dmps-1-discovery.csv", index=False)
    pd.DataFrame(
        {
            "chromosome": ["2"],
            "position": [300],
            "context": ["CG"],
            "effect_size": [0.7],
        }
    ).to_csv(det / "dmps-2-discovery.csv", index=False)
    pd.DataFrame(
        {
            "chromosome": ["1"],
            "position": [100],
            "context": ["CG"],
            "effect_size": [0.9],
        }
    ).to_csv(det / "dmps-1-classifier.csv", index=False)
    return run_dir


def test_load_discovery_dmp_panel_deduplicates_across_chromosomes(tmp_path: Path) -> None:
    run_dir = _write_detection_csvs(tmp_path)
    panel = load_discovery_dmp_panel(run_dir)
    assert panel is not None
    assert len(panel) == 3
    assert int(panel.iloc[0]["position"]) == 100


def test_load_classifier_dmp_panel_prefers_classifier_exports(tmp_path: Path) -> None:
    run_dir = _write_detection_csvs(tmp_path)
    panel = load_classifier_dmp_panel(run_dir)
    assert panel is not None
    assert len(panel) == 1


def test_load_classifier_dmp_panel_per_dir_extended_fallback(tmp_path: Path) -> None:
    """Extended preference is per detection dir, not global."""
    run_dir = tmp_path / "run_0001"
    det_extended = run_dir / "detections" / "all" / "PCa"
    det_core_only = run_dir / "detections" / "all" / "healthy"
    det_extended.mkdir(parents=True)
    det_core_only.mkdir(parents=True)
    pd.DataFrame(
        {
            "chromosome": ["1"],
            "position": [100],
            "context": ["CG"],
            "effect_size": [0.9],
        }
    ).to_csv(det_extended / "dmps-1-classifier-extended.csv", index=False)
    pd.DataFrame(
        {
            "chromosome": ["2"],
            "position": [200],
            "context": ["CG"],
            "effect_size": [0.7],
        }
    ).to_csv(det_core_only / "dmps-2-classifier.csv", index=False)

    panel = load_classifier_dmp_panel(run_dir)
    assert panel is not None
    assert len(panel) == 2
    assert set(int(p) for p in panel["position"]) == {100, 200}
