"""Unit tests for pure fragmentomics summary/writer helpers (no BAM I/O)."""

from __future__ import annotations

from pathlib import Path

from methyl_fragmentomics.core.end_motifs import summarize_end_motifs, write_end_motifs_tsv
from methyl_fragmentomics.core.wps import summarize_wps, write_wps_tsv


def test_summarize_end_motifs_reports_totals_and_top() -> None:
    counts = {"AAAA": 5, "CCCC": 3, "GGGG": 1}
    summary = summarize_end_motifs(counts, k=4)
    assert summary["end_motif_k"] == 4
    assert summary["total_fragments_scored"] == 9
    assert summary["unique_motifs"] == 3
    assert summary["top_motifs"][0] == {"motif": "AAAA", "count": 5}


def test_write_end_motifs_tsv_sorted_with_fractions(tmp_path: Path) -> None:
    out = tmp_path / "motifs.tsv"
    write_end_motifs_tsv({"AAAA": 3, "CCCC": 1}, out)
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "motif\tcount\tfraction"
    # Highest count first; fraction = count/total (total=4).
    assert lines[1].startswith("AAAA\t3\t0.750000")
    assert lines[2].startswith("CCCC\t1\t0.250000")


def test_summarize_wps_empty_bins() -> None:
    summary = summarize_wps({}, bin_bp=1000)
    assert summary == {"wps_bin_bp": 1000, "n_bins": 0, "total_fragments": 0}


def test_summarize_wps_aggregates_bins() -> None:
    bins = {("1", 0): 4, ("1", 1): 2}
    summary = summarize_wps(bins, bin_bp=1000)
    assert summary["n_bins"] == 2
    assert summary["total_fragments"] == 6
    assert summary["mean_fragments_per_bin"] == 3.0
    assert summary["max_bin_count"] == 4


def test_write_wps_tsv_computes_bin_bounds(tmp_path: Path) -> None:
    out = tmp_path / "wps.tsv"
    write_wps_tsv({("1", 2): 7}, out, bin_bp=1000)
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "chrom\tbin_start\tbin_end\tfragment_count"
    assert lines[1] == "1\t2000\t3000\t7"
