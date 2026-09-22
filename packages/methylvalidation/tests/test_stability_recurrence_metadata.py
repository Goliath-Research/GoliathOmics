from pathlib import Path

import pandas as pd
import pytest

from methyl_validation.stability import _merge_stable_dmp_panels


def _write_panel(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def test_merge_stable_panels_rejects_count_greater_than_n_runs(tmp_path):
    src = tmp_path / "stable_dmps_production.csv"
    _write_panel(
        src,
        [
            {"chromosome": 1, "position": 100, "frequency": 1.0, "count": 12, "n_runs": 10},
        ],
    )

    with pytest.raises(ValueError, match="count"):
        _merge_stable_dmp_panels(src, tmp_path / "production")


def test_merge_stable_panels_rejects_frequency_ratio_mismatch(tmp_path):
    src = tmp_path / "stable_dmps_production.csv"
    _write_panel(
        src,
        [
            {"chromosome": 1, "position": 100, "frequency": 0.7, "count": 8, "n_runs": 10},
        ],
    )

    with pytest.raises(ValueError, match="count / n_runs"):
        _merge_stable_dmp_panels(src, tmp_path / "production")


def test_merge_stable_panels_preserves_valid_recurrence_metadata(tmp_path):
    src = tmp_path / "stable_dmps_production.csv"
    _write_panel(
        src,
        [
            {"chromosome": 1, "position": 100, "frequency": 0.8, "count": 8, "n_runs": 10},
            {"chromosome": 1, "position": 200, "frequency": 1.0, "count": 10, "n_runs": 10},
        ],
    )

    out = _merge_stable_dmp_panels(src, tmp_path / "production")
    merged = pd.read_csv(out)
    assert len(merged) == 2
    assert merged["frequency"].max() == 1.0
    assert (merged["count"] <= merged["n_runs"]).all()
