"""Regression: strict detector config — removed keys error; canonical keys validate."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from pydantic import ValidationError

from methyl_detector.models.config import MethylDetectorConfig


def _minimal_detector_dict(temp_dir: Path) -> dict:
    c1 = temp_dir / "c1"
    c2 = temp_dir / "c2"
    c1.mkdir()
    c2.mkdir()
    return {
        "chromosome": "1",
        "contexts": ["CG"],
        "centroid1_dir": str(c1),
        "centroid2_dir": str(c2),
        "output_dir": str(temp_dir / "out"),
    }


@pytest.mark.parametrize(
    "bad_key,bad_value,match_substr",
    [
        ("use_gpu", True, "use_gpu was removed"),
        ("max_dmps_for_classifier", 10000, "max_dmps_for_classifier was removed"),
        ("validation_mode", "real", "validation_mode was removed"),
        ("n_validation_samples", 50, "n_validation_samples was removed"),
        ("min_sample_coverage", 4, "min_sample_coverage was removed"),
        ("classifier_coverage_weighting", False, "classifier_coverage_weighting was removed"),
        ("min_validation_coverage_per_position", 3, "min_validation_coverage_per_position was removed"),
        ("synthetic_config", {"realism_level": "basic"}, "synthetic_config was removed"),
        ("classifier_type", "ecdf", "classifier_type was removed"),
        ("eps", 1e-9, "eps was removed"),
    ],
)
def test_rejects_removed_detector_keys(bad_key, bad_value, match_substr):
    with TemporaryDirectory() as td:
        temp = Path(td)
        d = _minimal_detector_dict(temp)
        d[bad_key] = bad_value
        with pytest.raises(ValueError, match=match_substr):
            MethylDetectorConfig.model_validate(d)


def test_rejects_unknown_extra_key():
    with TemporaryDirectory() as td:
        temp = Path(td)
        d = _minimal_detector_dict(temp)
        d["not_a_real_detector_field"] = 1
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            MethylDetectorConfig.model_validate(d)


def test_canonical_ecdf_and_featurecuts_keys_validate():
    with TemporaryDirectory() as td:
        temp = Path(td)
        d = _minimal_detector_dict(temp)
        d.update(
            {
                "ecdf_grid_size": 256,
                "effect_size_coverage": 0.95,
                "classifier_dmp_selection": "featurecuts_validation",
                "featurecuts_max_k_cap": 5000,
                "target_balanced_accuracy": 0.85,
                "validation_split_ratio": 0.2,
                "validation_n_repeats": 2,
            }
        )
        cfg = MethylDetectorConfig.model_validate(d)
        assert cfg.ecdf_grid_size == 256
        assert cfg.classifier_dmp_selection == "featurecuts_validation"
        assert cfg.validation_n_repeats == 2
