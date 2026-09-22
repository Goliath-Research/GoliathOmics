"""Filter funnel sweep: per-chromosome CSV and genome-wide aggregate."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from methyl_detector.core.methyldetector import MethylDetector
from methyl_detector.models.config import MethylDetectorConfig


def _minimal_cfg(temp: Path, chromosome, **extra) -> MethylDetectorConfig:
    c1 = temp / "c1"
    c2 = temp / "c2"
    out = temp / "out"
    c1.mkdir()
    c2.mkdir()
    out.mkdir(exist_ok=True)
    base = {
        "chromosome": chromosome,
        "contexts": ["CG"],
        "centroid1_dir": str(c1),
        "centroid2_dir": str(c2),
        "output_dir": str(out),
        "filter_funnel_explore": {
            "effect_size_coverage": {"min": 0.8, "max": 0.9, "step": 0.1},
        },
    }
    base.update(extra)
    return MethylDetectorConfig.model_validate(base)


def test_filter_funnel_sweep_writes_chromosome_specific_csv():
    with TemporaryDirectory() as td:
        temp = Path(td)
        cfg = _minimal_cfg(temp, "7")
        detector = MethylDetector(cfg)
        dmps_df = pd.DataFrame(
            {"effect_size": [1.0, 0.5, 0.2], "context": ["CG", "CG", "CG"]}
        )
        detector._run_filter_funnel_sweep(dmps_df)

        chrom_path = temp / "out" / "filter_funnel-7.csv"
        assert chrom_path.is_file()
        written = pd.read_csv(chrom_path)
        assert list(written.columns) == [
            "n_statistical_dmps",
            "effect_size_coverage",
            "n_biological_dmps",
        ]
        assert len(written) == 2


def test_filter_funnel_aggregate_combines_per_chrom_files():
    with TemporaryDirectory() as td:
        temp = Path(td)
        cfg = _minimal_cfg(temp, ["1", "2"])
        detector = MethylDetector(cfg)
        out = temp / "out"

        pd.DataFrame(
            {
                "n_statistical_dmps": [10, 10],
                "effect_size_coverage": [0.8, 0.9],
                "n_biological_dmps": [5, 7],
            }
        ).to_csv(out / "filter_funnel-1.csv", index=False)
        pd.DataFrame(
            {
                "n_statistical_dmps": [20, 20],
                "effect_size_coverage": [0.8, 0.9],
                "n_biological_dmps": [8, 12],
            }
        ).to_csv(out / "filter_funnel-2.csv", index=False)

        detector._aggregate_filter_funnel_csvs(["1", "2"])

        agg = pd.read_csv(out / "filter_funnel.csv")
        assert list(agg.columns) == [
            "chromosome",
            "n_statistical_dmps",
            "effect_size_coverage",
            "n_biological_dmps",
        ]
        assert agg["chromosome"].astype(str).tolist() == ["1", "1", "2", "2"]
        assert len(agg) == 4


def test_filter_funnel_sweep_then_aggregate_single_chromosome():
    with TemporaryDirectory() as td:
        temp = Path(td)
        cfg = _minimal_cfg(temp, "7")
        detector = MethylDetector(cfg)
        dmps_df = pd.DataFrame(
            {"effect_size": [1.0, 0.5, 0.2], "context": ["CG", "CG", "CG"]}
        )
        detector._run_filter_funnel_sweep(dmps_df)
        detector._aggregate_filter_funnel_csvs(["7"])

        agg = pd.read_csv(temp / "out" / "filter_funnel.csv")
        assert list(agg.columns)[0] == "chromosome"
        assert agg["chromosome"].astype(str).eq("7").all()
        assert len(agg) == 2
