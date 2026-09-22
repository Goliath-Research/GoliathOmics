"""Label-free confounder score panels and coverage flags."""

from __future__ import annotations

from pathlib import Path

from methyl_utils.confounder_scores import load_panel, packaged_panel_path, run_confounder_scores
from methyl_utils.tests.test_centroid_builder import create_temp_sample


def test_load_panel_resolves_packaged_filename() -> None:
    panel = load_panel("smoking_ahr_v1.json")
    assert panel.score_name == "smoking_score"
    assert any(s.illumina_id == "cg05575921" for s in panel.sites)


def test_hannum2013_panel_is_real_blood_clock() -> None:
    panel = load_panel("hannum2013_v1.json")
    assert panel.panel_id == "hannum2013_v1"
    assert panel.score_name == "age_score"
    assert panel.score_kind == "weighted_beta"
    assert panel.genome == "GRCh38"
    assert len(panel.sites) == 71
    assert all(s.weight != 0.0 for s in panel.sites)
    chroms = {s.chrom for s in panel.sites}
    assert chroms <= {str(i) for i in range(1, 23)} | {"X", "Y", "M", "MT"}
    assert all(int(s.position) > 0 for s in panel.sites)
    assert any(s.illumina_id == "cg16867657" for s in panel.sites)


def test_confounder_scores_write_status_flags(tmp_path: Path) -> None:
    sample_dir = tmp_path / "S1"
    sample_dir.mkdir()
    h5 = create_temp_sample([373323], [10], [90], [0])
    dest = sample_dir / "5-CG.h5"
    dest.write_bytes(Path(h5).read_bytes())
    panel = load_panel(packaged_panel_path("smoking_ahr_v1.json"))
    out = tmp_path / "scores"
    summary = run_confounder_scores(
        [("S1", str(sample_dir))],
        out,
        panels={"smoking_score": panel},
        contexts=["CG"],
        min_coverage=4,
        min_sites_fraction=1.0,
    )
    assert summary["n_samples"] == 1
    csv_path = Path(summary["output_csv"])
    assert csv_path.is_file()
    text = csv_path.read_text(encoding="utf-8")
    assert "smoking_score" in text
    assert "score_status" in text
    assert "insufficient_markers" in text
