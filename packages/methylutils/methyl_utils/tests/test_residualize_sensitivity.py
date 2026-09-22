"""Unadjusted vs residualized DMP Jaccard for methyl-residualize-sensitivity."""

from __future__ import annotations

from methyl_utils.cli_residualize_sensitivity import _jaccard, _loci
import pandas as pd


def test_jaccard_and_loci_helpers() -> None:
    u = pd.DataFrame({"chromosome": ["1", "1", "2"], "position": [10, 20, 30]})
    a = pd.DataFrame({"chromosome": ["1", "2"], "position": [10, 40]})
    assert _jaccard(_loci(u), _loci(a)) == 1 / 4
    assert "1:10" in _loci(u) & _loci(a)
