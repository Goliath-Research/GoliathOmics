"""Focused regression tests for active MethylClassifier behavior."""

import csv
import importlib
from pathlib import Path

import numpy as np

from methyl_classifier.cli.main import classify_samples_from_list
from methyl_classifier.core.classifier import extract_chrom_context_from_classifier
from methyl_classifier.utils.data_loader import DataLoader
from methyl_utils import MethylSample


class _DummyInnerClassifier:
    def get_feature_info(self):
        return {"positions": np.array([10, 20], dtype=np.uint32)}


class _DummyClassifier:
    def __init__(self):
        self.is_multi_chromosome = False
        self.classifier = _DummyInnerClassifier()
        self.chromosome = "1"
        self.n_classes = 2
        self.class_names = ["control", "disease"]

    def get_feature_info(self):
        return self.classifier.get_feature_info()


def test_extract_sample_features_dmp_past_last_position_no_index_error():
    """Regression: searchsorted can return len(sp); np.where evaluated sm[safe_idx] eagerly and could mis-index."""
    n = 278
    pos = np.arange(n, dtype=np.uint32)
    mC = np.ones(n, dtype=np.uint32) * 3
    uC = np.ones(n, dtype=np.uint32) * 3
    tnc = np.zeros(n, dtype=np.uint8)
    sample = MethylSample.from_sample_data(pos, mC, uC, tnc)
    dmp = np.array([999_999_999], dtype=np.uint32)
    feats, mask, _ = DataLoader.extract_sample_features(sample, dmp)
    assert feats.shape == (1,) and mask.shape == (1,) and not bool(mask[0])


def test_chromosome_keys_to_str_unifies_int_and_str_keys():
    from methyl_classifier.cli.main import _chromosome_keys_to_str

    d = {1: "a", "2": "b", 22: "c"}
    out = _chromosome_keys_to_str(d)
    assert out["1"] == "a"
    assert out["2"] == "b"
    assert out["22"] == "c"


def test_extract_chrom_context_from_classifier_fallback():
    path = Path("/path/to/pb-ch-2-CG/methyl_detector_classifier.pkl")
    chrom, context = extract_chrom_context_from_classifier(path)
    assert chrom == "2"
    assert context == "CG"


def test_classify_samples_from_list_realigns_expected_classes(monkeypatch, tmp_path):
    classifier = _DummyClassifier()
    output_file = tmp_path / "predictions.csv"
    classifier_cli_main = importlib.import_module("methyl_classifier.cli.main")

    loaded_samples = [
        ("sample-control", {"1": object()}),
        ("sample-disease", {"1": object()}),
    ]

    monkeypatch.setattr(
        DataLoader,
        "load_samples_from_list",
        staticmethod(lambda *args, **kwargs: (loaded_samples, [0, 2])),
    )
    monkeypatch.setattr(
        DataLoader,
        "extract_sample_features",
        staticmethod(
            lambda sample, dmp_positions: (
                np.array([0.1, 0.2], dtype=np.float64),
                np.array([True, True], dtype=bool),
                {},
            )
        ),
    )

    def fake_classify_samples_batch(
        classifier_obj,
        feature_matrix,
        availability_mask,
        debug=False,
        expected_classes=None,
        min_observed_dmp_fraction=0.0,
    ):
        predictions = np.array([0, 1], dtype=np.int64)
        probabilities = np.array([[0.9, 0.1], [0.2, 0.8]], dtype=np.float64)
        abst = np.zeros(2, dtype=bool)
        return predictions, probabilities, abst

    monkeypatch.setattr(classifier_cli_main, "classify_samples_batch", fake_classify_samples_batch)

    classify_samples_from_list(
        classifier=classifier,
        samples_list=["/samples/s0", "/samples/s1", "/samples/s2"],
        output_file=output_file,
        expected_classes=[0, 0, 1],
    )

    with output_file.open(newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert [row["sample"] for row in rows] == ["sample-control", "sample-disease"]
    assert [int(row["expected_class"]) for row in rows] == [0, 1]
    assert [row["agrees"] for row in rows] == ["True", "True"]
