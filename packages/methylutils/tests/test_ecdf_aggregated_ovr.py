from __future__ import annotations

import numpy as np
import pandas as pd

from methyl_utils.ecdf_aggregated_ovr import (
    AGGREGATED_ECDF_OVR_TYPE,
    GENE_ECDF_OVR_TYPE,
    build_effect_size_feature_weights,
    predict_aggregated_ecdf_ovr_proba,
    train_aggregated_ecdf_ovr_package,
)


def _toy_dmp_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "effect_size": [0.2, -0.4, 0.8, -1.1, 0.6],
            "gene_name": ["A", "A", "B", "unknown", "C"],
            "feature_type": ["promoter", "exon", "intron", "gene_body", "terminator"],
        }
    )


def test_train_and_predict_aggregated_package_roundtrip() -> None:
    X = np.asarray(
        [
            [0.2, 0.1, 0.8],
            [0.3, 0.2, 0.7],
            [0.8, 0.7, 0.2],
            [0.9, 0.8, 0.1],
        ],
        dtype=np.float64,
    )
    y = np.asarray([0, 0, 1, 1], dtype=np.int32)
    feature_names = ["gene_obs_fraction", "struct_promoter_obs_fraction", "max_weighted_directional_score"]
    weights = build_effect_size_feature_weights(_toy_dmp_df(), feature_names)

    pkg = train_aggregated_ecdf_ovr_package(
        X,
        y,
        class_names=["healthy", "disease"],
        feature_names=feature_names,
        feature_weights=weights,
        feature_family_set="gene_scored",
        feature_mode="observed_hybrid",
        n_bins=32,
        temperature=1.0,
    )
    assert pkg["classifier_type"] == AGGREGATED_ECDF_OVR_TYPE
    assert pkg["feature_schema"]["feature_names"] == feature_names
    assert len(pkg["binary_models"]) == 2

    probs, evidence = predict_aggregated_ecdf_ovr_proba(pkg, X)
    assert probs.shape == (4, 2)
    assert evidence.shape == (4, 2)
    np.testing.assert_allclose(np.sum(probs, axis=1), np.ones((4,)), rtol=1e-6, atol=1e-6)
    stabilized = evidence - np.max(evidence, axis=1, keepdims=True)
    assert not np.allclose(
        evidence,
        stabilized,
    ), "evidence logits must be raw pre-softmax values, not max-shifted"
    pred = np.argmax(probs, axis=1)
    assert set(pred.tolist()) <= {0, 1}


def test_train_and_predict_gene_ecdf_package_roundtrip() -> None:
    X = np.asarray(
        [
            [0.2, 0.8],
            [0.3, 0.7],
            [0.8, 0.2],
            [0.9, 0.1],
        ],
        dtype=np.float64,
    )
    y = np.asarray([0, 0, 1, 1], dtype=np.int32)
    feature_names = ["gene::GENE1", "gene::GENE2"]
    weights = np.asarray([1.0, 1.0], dtype=np.float64)

    pkg = train_aggregated_ecdf_ovr_package(
        X,
        y,
        class_names=["healthy", "disease"],
        feature_names=feature_names,
        feature_weights=weights,
        feature_family_set="gene_scored",
        feature_mode="raw_gene",
        classifier_type=GENE_ECDF_OVR_TYPE,
    )
    assert pkg["classifier_type"] == GENE_ECDF_OVR_TYPE
    probs, _ = predict_aggregated_ecdf_ovr_proba(pkg, X)
    assert probs.shape == (4, 2)


def test_build_effect_size_feature_weights_supports_dynamic_keys() -> None:
    dmp_df = pd.DataFrame(
        {
            "effect_size": [1.0, -2.0, 4.0],
            "gene_name": ["A", "A", "B"],
            "feature_type": ["promoter", "exon", "promoter"],
        }
    )
    names = [
        "gene::A",
        "gene::B",
        "struct::A::promoter",
        "struct::A::exon",
        "struct::B::promoter",
        "gene_weighted_shift_vs_healthy",
    ]
    weights = build_effect_size_feature_weights(dmp_df, names)
    # Normalized by max(4.0), preserving per-key relative means.
    np.testing.assert_allclose(
        weights,
        np.asarray(
            [
                1.5 / 4.0,
                4.0 / 4.0,
                1.0 / 4.0,
                2.0 / 4.0,
                4.0 / 4.0,
                ((1.0 + 2.0 + 4.0) / 3.0) / 4.0,
            ],
            dtype=np.float64,
        ),
        rtol=1e-6,
        atol=1e-6,
    )


def test_build_effect_size_feature_weights_struct_keys_use_canonical_feature_aliases() -> None:
    dmp_df = pd.DataFrame(
        {
            "effect_size": [2.0, 10.0, 4.0],
            "gene_name": ["A", "A", "A"],
            "feature_type": ["promoter_region", "exon", "genebody"],
        }
    )
    names = [
        "struct::A::promoter",
        "struct::A::exon",
        "struct::A::gene_body",
    ]
    weights = build_effect_size_feature_weights(dmp_df, names)
    np.testing.assert_allclose(
        weights,
        np.asarray([0.2, 1.0, 0.4], dtype=np.float64),
        rtol=1e-6,
        atol=1e-6,
    )
