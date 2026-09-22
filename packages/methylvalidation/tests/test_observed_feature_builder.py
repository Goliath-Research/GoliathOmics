from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from methyl_validation import observed_feature_builder


def _fake_extract_complete(sample_paths, reference_positions, chromosome, min_coverage=1):
    del chromosome, min_coverage
    rows = []
    ctxs = []
    poss = []
    for ctx in sorted(reference_positions.keys()):
        for p in list(np.asarray(reference_positions[ctx], dtype=np.uint32)):
            ctxs.append(ctx)
            poss.append(int(p))
    for s in sample_paths:
        sid = Path(str(s)).name
        if sid in {"S1", "S2"}:
            vals = [0.10, 0.20, 0.30, 0.40]
        else:
            vals = [0.70, 0.80, 0.90, 0.95]
        rows.append(vals[: len(poss)])
    X = np.asarray(rows, dtype=np.float32)
    return (
        X,
        np.asarray(poss, dtype=np.uint32),
        np.asarray(ctxs, dtype=object),
        {"CG": np.arange(len(poss), dtype=np.uint32)},
    )


def _fake_extract_missing(sample_paths, reference_positions, chromosome, min_coverage=1):
    del chromosome, min_coverage
    # Return only one locus even if more were requested to simulate partial observation.
    pos = np.asarray([int(np.asarray(reference_positions["CG"])[0])], dtype=np.uint32)
    ctx = np.asarray(["CG"], dtype=object)
    X = np.asarray([[0.25], [0.75]], dtype=np.float32)
    return X, pos, ctx, {"CG": np.arange(1, dtype=np.uint32)}


def _dmp_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "chromosome": ["1", "1", "2", "2"],
            "context": ["CG", "CG", "CG", "CG"],
            "position": [100, 120, 200, 220],
            "weight": [1.0, 0.8, 0.7, 0.5],
            "effect_size": [1.0, 0.8, 0.7, 0.5],
        }
    )


def test_range_loading_expands_loci_beyond_frozen_dmps(monkeypatch):
    monkeypatch.setattr(
        observed_feature_builder,
        "_resolve_sample_context_h5",
        lambda sample_path, chromosome, context: Path("/tmp/fake.h5"),
    )
    monkeypatch.setattr(
        observed_feature_builder,
        "load_from_h5",
        lambda path: SimpleNamespace(df=pd.DataFrame({"pos": [95, 100, 110, 500]})),
    )
    base = pd.DataFrame(
        {
            "comparison_label": ["healthy_vs_pca1"],
            "chromosome": ["1"],
            "context": ["CG"],
            "position": [100],
            "effect_size": [0.4],
            "gene_name": ["GENE_A"],
            "feature_type": ["promoter"],
        }
    )
    ranges = pd.DataFrame(
        {
            "comparison_label": ["healthy_vs_pca1"],
            "gene_name": ["GENE_A"],
            "chromosome": ["1"],
            "feature_type": ["promoter"],
            "feature_start": [90],
            "feature_end": [120],
            "feature_effect_compound": [0.8],
        }
    )
    expanded = observed_feature_builder._expand_loci_df_from_gene_ranges(
        sample_paths=["/tmp/S1"],
        base_dmp_df=base,
        gene_feature_ranges_df=ranges,
    )
    assert sorted(set(expanded["position"].astype(int).tolist())) == [95, 100, 110]


def test_anchor_and_feature_table_do_not_diverge_for_dmp_family_with_range_loading(monkeypatch):
    monkeypatch.setattr(
        observed_feature_builder,
        "_resolve_sample_context_h5",
        lambda sample_path, chromosome, context: Path("/tmp/fake.h5"),
    )
    monkeypatch.setattr(
        observed_feature_builder,
        "load_from_h5",
        lambda path: SimpleNamespace(df=pd.DataFrame({"pos": [95, 100, 110, 500]})),
    )
    monkeypatch.setattr(
        observed_feature_builder.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract_complete,
    )

    sample_paths = ["/tmp/S1", "/tmp/S2", "/tmp/S3", "/tmp/S4"]
    y = [0, 0, 1, 1]
    class_names = ["healthy", "cancer"]
    dmp_df = pd.DataFrame(
        {
            "comparison_label": ["healthy_vs_cancer"],
            "chromosome": ["1"],
            "context": ["CG"],
            "position": [100],
            "effect_size": [0.4],
            "gene_name": ["GENE_A"],
            "feature_type": ["promoter"],
        }
    )
    fixed_gene_features_df = pd.DataFrame(
        {
            "comparison_label": ["healthy_vs_cancer"],
            "gene_name": ["GENE_A"],
            "chromosome": ["1"],
            "feature_type": ["promoter"],
            "feature_start": [90],
            "feature_end": [120],
            "feature_effect_compound": [0.8],
        }
    )

    anchors = observed_feature_builder.derive_observed_hybrid_anchors(
        sample_paths=sample_paths,
        sample_class_indices=y,
        class_names=class_names,
        dmp_df=dmp_df,
        min_coverage=1,
        feature_family_set="dmp_scored",
        gene_feature_loading="range",
        fixed_gene_features_df=fixed_gene_features_df,
    )
    # This should not raise due to reference length mismatch / fingerprint mismatch.
    feat = observed_feature_builder.build_observed_hybrid_feature_table(
        sample_paths,
        dmp_df,
        healthy_reference_vector=anchors.healthy_reference_vector,
        cancer_reference_vector=anchors.cancer_reference_vector,
        healthy_class_label=anchors.healthy_class_label,
        cancer_class_labels=anchors.cancer_class_labels,
        anchor_strategy=anchors.anchor_strategy,
        expected_feature_order_fingerprint=anchors.feature_order_fingerprint,
        feature_family_set="dmp_scored",
        gene_feature_loading="range",
        fixed_gene_features_df=fixed_gene_features_df,
    )
    assert feat.X.shape[0] == len(sample_paths)


def _derive_anchors(sample_paths, y, class_names, dmp_df):
    return observed_feature_builder.derive_observed_hybrid_anchors(
        sample_paths=sample_paths,
        sample_class_indices=y,
        class_names=class_names,
        dmp_df=dmp_df,
        min_coverage=1,
    )


def test_observed_feature_schema_is_deterministic(monkeypatch):
    monkeypatch.setattr(
        observed_feature_builder.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract_complete,
    )
    sample_paths = ["/tmp/S1", "/tmp/S2", "/tmp/S3", "/tmp/S4"]
    y = [0, 0, 1, 1]
    class_names = ["healthy", "cancer"]
    dmp_df = _dmp_df()
    anchors = _derive_anchors(sample_paths, y, class_names, dmp_df)

    feat_a = observed_feature_builder.build_observed_hybrid_feature_table(
        sample_paths,
        dmp_df,
        healthy_reference_vector=anchors.healthy_reference_vector,
        cancer_reference_vector=anchors.cancer_reference_vector,
        healthy_class_label=anchors.healthy_class_label,
        cancer_class_labels=anchors.cancer_class_labels,
        anchor_strategy=anchors.anchor_strategy,
        expected_feature_order_fingerprint=anchors.feature_order_fingerprint,
    )
    feat_b = observed_feature_builder.build_observed_hybrid_feature_table(
        sample_paths,
        dmp_df.sample(frac=1.0, random_state=13).reset_index(drop=True),
        healthy_reference_vector=anchors.healthy_reference_vector,
        cancer_reference_vector=anchors.cancer_reference_vector,
        healthy_class_label=anchors.healthy_class_label,
        cancer_class_labels=anchors.cancer_class_labels,
        anchor_strategy=anchors.anchor_strategy,
        expected_feature_order_fingerprint=anchors.feature_order_fingerprint,
    )
    assert feat_a.feature_names == feat_b.feature_names
    assert feat_a.report["schema_fingerprint"] == feat_b.report["schema_fingerprint"]
    assert feat_a.X.shape == feat_b.X.shape


def test_observed_feature_builder_keeps_missing_loci_as_nan(monkeypatch):
    monkeypatch.setattr(
        observed_feature_builder.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract_missing,
    )
    sample_paths = ["/tmp/S1", "/tmp/S2"]
    y = [0, 1]
    class_names = ["healthy", "cancer"]
    dmp_df = pd.DataFrame(
        {
            "chromosome": ["1", "1", "1"],
            "context": ["CG", "CG", "CG"],
            "position": [100, 120, 140],
            "weight": [1.0, 1.0, 1.0],
            "effect_size": [1.0, 1.0, 1.0],
        }
    )
    anchors = _derive_anchors(sample_paths, y, class_names, dmp_df)
    feat = observed_feature_builder.build_observed_hybrid_feature_table(
        sample_paths,
        dmp_df,
        healthy_reference_vector=anchors.healthy_reference_vector,
        cancer_reference_vector=anchors.cancer_reference_vector,
        healthy_class_label=anchors.healthy_class_label,
        cancer_class_labels=anchors.cancer_class_labels,
        anchor_strategy=anchors.anchor_strategy,
        expected_feature_order_fingerprint=anchors.feature_order_fingerprint,
    )
    obs_idx = feat.feature_names.index("obs_fraction")
    total_idx = feat.feature_names.index("n_total_dmps")
    assert np.allclose(feat.X[:, obs_idx], np.asarray([1.0 / 3.0, 1.0 / 3.0], dtype=np.float32), atol=1e-5)
    assert np.allclose(feat.X[:, total_idx], np.asarray([3.0, 3.0], dtype=np.float32), atol=1e-6)


def test_verify_feature_schema_raises_on_mismatch():
    with pytest.raises(ValueError, match="schema mismatch"):
        observed_feature_builder.verify_feature_schema(
            ["a", "b", "c"],
            ["a", "x", "c"],
            context="unit-test",
        )


def test_observed_feature_builder_requires_effect_size(monkeypatch):
    monkeypatch.setattr(
        observed_feature_builder.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract_complete,
    )
    sample_paths = ["/tmp/S1", "/tmp/S2", "/tmp/S3", "/tmp/S4"]
    y = [0, 0, 1, 1]
    class_names = ["healthy", "cancer"]
    dmp_df = _dmp_df().drop(columns=["effect_size"])

    with pytest.raises(ValueError, match="effect_size"):
        _derive_anchors(sample_paths, y, class_names, dmp_df)


def test_observed_feature_builder_includes_fixed_schema_and_centroid_metrics(monkeypatch):
    monkeypatch.setattr(
        observed_feature_builder.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract_complete,
    )
    sample_paths = ["/tmp/S1", "/tmp/S2", "/tmp/S3", "/tmp/S4"]
    y = [0, 0, 1, 1]
    class_names = ["healthy", "cancer"]
    dmp_df = _dmp_df()
    anchors = _derive_anchors(sample_paths, y, class_names, dmp_df)
    feat = observed_feature_builder.build_observed_hybrid_feature_table(
        sample_paths,
        dmp_df,
        healthy_reference_vector=anchors.healthy_reference_vector,
        cancer_reference_vector=anchors.cancer_reference_vector,
        healthy_class_label=anchors.healthy_class_label,
        cancer_class_labels=anchors.cancer_class_labels,
        anchor_strategy=anchors.anchor_strategy,
        expected_feature_order_fingerprint=anchors.feature_order_fingerprint,
    )

    expected_names = {
        "max_weighted_directional_score",
        "weighted_directional_agreement__cancer",
        "weighted_cosine_similarity_to_cancer_centroid__cancer",
        "weighted_centroid_contrast_score",
        "obs_fraction",
        "n_obs_dmps",
        "n_total_dmps",
        "weighted_healthy_tail_evidence__cancer",
    }
    assert expected_names.issubset(set(feat.feature_names))
    removed_names = {
        "weighted_mean_abs_distance_margin",
        "weighted_obs_fraction",
        "weighted_fraction_dmps_closer_to_cancer_centroid__cancer",
        "weighted_mean_abs_error_to_cancer_centroid__cancer",
    }
    assert removed_names.isdisjoint(set(feat.feature_names))
    for quality_name in ("obs_fraction", "n_obs_dmps", "n_total_dmps"):
        assert quality_name in feat.feature_names
        assert quality_name in feat.quality_feature_names
        assert quality_name not in feat.training_feature_names

    wcontrast_idx = feat.feature_names.index("weighted_centroid_contrast_score")
    max_wds_idx = feat.feature_names.index("max_weighted_directional_score")
    wda_idx = feat.feature_names.index("weighted_directional_agreement__cancer")
    tail_e_idx = feat.feature_names.index("weighted_healthy_tail_evidence__cancer")

    # First sample is healthy-like and should be closer to healthy anchor.
    assert float(feat.X[0, wcontrast_idx]) < 0.0
    assert float(feat.X[0, max_wds_idx]) < 0.0
    assert float(feat.X[0, wda_idx]) < 0.5
    assert not np.isfinite(float(feat.X[0, tail_e_idx]))

    # Cancer-like sample should move towards cancer anchor.
    assert float(feat.X[2, wcontrast_idx]) > 0.0
    assert float(feat.X[2, max_wds_idx]) > 0.0
    assert float(feat.X[2, wda_idx]) > 0.5
    assert not np.isfinite(float(feat.X[2, tail_e_idx]))

    profile = feat.report.get("feature_profile") or {}
    assert profile.get("schema_version") == observed_feature_builder.HYBRID_FEATURE_SCHEMA_VERSION
    assert profile.get("n_training_features") == len(feat.training_feature_names)
    assert profile.get("n_quality_features") == len(feat.quality_feature_names)


def test_observed_feature_builder_histogram_tail_features_multiclass_names(monkeypatch):
    monkeypatch.setattr(
        observed_feature_builder.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract_complete,
    )
    sample_paths = ["/tmp/S1", "/tmp/S2", "/tmp/S3", "/tmp/S4", "/tmp/S5", "/tmp/S6"]
    y = [0, 0, 1, 1, 2, 2]
    class_names = ["healthy", "pca1", "pca2"]
    dmp_df = _dmp_df()
    anchors = _derive_anchors(sample_paths, y, class_names, dmp_df)
    feat = observed_feature_builder.build_observed_hybrid_feature_table(
        sample_paths,
        dmp_df,
        healthy_reference_vector=anchors.healthy_reference_vector,
        cancer_reference_vector=anchors.cancer_reference_vector,
        per_cancer_reference_vectors=anchors.per_cancer_reference_vectors,
        healthy_class_label=anchors.healthy_class_label,
        cancer_class_labels=anchors.cancer_class_labels,
        anchor_strategy=anchors.anchor_strategy,
        expected_feature_order_fingerprint=anchors.feature_order_fingerprint,
    )
    assert "weighted_healthy_tail_evidence__pca1" in feat.feature_names
    assert "weighted_directional_agreement__pca1" in feat.feature_names
    assert "weighted_cosine_similarity_to_cancer_centroid__pca1" in feat.feature_names
    assert "weighted_healthy_tail_evidence__pca2" in feat.feature_names
    assert "weighted_directional_agreement__pca2" in feat.feature_names
    assert "weighted_cosine_similarity_to_cancer_centroid__pca2" in feat.feature_names
    assert "weighted_mean_abs_error_to_cancer_centroid__pca1" not in feat.feature_names
    assert "weighted_fraction_dmps_closer_to_cancer_centroid__pca1" not in feat.feature_names
    assert "weighted_mean_abs_error_to_cancer_centroid__pca2" not in feat.feature_names
    assert "weighted_fraction_dmps_closer_to_cancer_centroid__pca2" not in feat.feature_names
    assert "weighted_cosine_distance_to_centroid__healthy" in feat.feature_names
    assert "weighted_cosine_distance_to_centroid__pca1" in feat.feature_names
    assert "weighted_cosine_distance_to_centroid__pca2" in feat.feature_names


def _fake_extract_with_centroid_profiles(sample_paths, reference_positions, chromosome, min_coverage=1):
    del chromosome, min_coverage
    rows = []
    ctxs = []
    poss = []
    for ctx in sorted(reference_positions.keys()):
        for p in list(np.asarray(reference_positions[ctx], dtype=np.uint32)):
            ctxs.append(ctx)
            poss.append(int(p))
    n_pos = len(poss)
    for s in sample_paths:
        token = Path(str(s)).name
        if token.endswith("healthy") or token in {"S1", "S2"}:
            vals = [0.10, 0.20, 0.30, 0.40]
        elif token.endswith("pca1") or token in {"S3", "S4"}:
            vals = [0.70, 0.80, 0.90, 0.95]
        elif token.endswith("pca2") or token in {"S5", "S6"}:
            vals = [0.85, 0.88, 0.92, 0.94]
        else:
            vals = [0.50] * n_pos
        rows.append(vals[:n_pos])
    X = np.asarray(rows, dtype=np.float32)
    return (
        X,
        np.asarray(poss, dtype=np.uint32),
        np.asarray(ctxs, dtype=object),
        {"CG": np.arange(n_pos, dtype=np.uint32)},
    )


def test_observed_feature_builder_centroid_distance_features_schema_and_behavior(monkeypatch):
    monkeypatch.setattr(
        observed_feature_builder.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract_with_centroid_profiles,
    )
    sample_paths = ["/tmp/S1", "/tmp/S2", "/tmp/S3", "/tmp/S4", "/tmp/S5", "/tmp/S6"]
    y = [0, 0, 1, 1, 2, 2]
    class_names = ["healthy", "pca1", "pca2"]
    dmp_df = _dmp_df()
    anchors = _derive_anchors(sample_paths, y, class_names, dmp_df)
    centroid_dirs = {
        "healthy": "/tmp/centroids/healthy",
        "pca1": "/tmp/centroids/pca1",
        "pca2": "/tmp/centroids/pca2",
    }
    feat = observed_feature_builder.build_observed_hybrid_feature_table(
        sample_paths,
        dmp_df,
        healthy_reference_vector=anchors.healthy_reference_vector,
        cancer_reference_vector=anchors.cancer_reference_vector,
        per_cancer_reference_vectors=anchors.per_cancer_reference_vectors,
        healthy_class_label=anchors.healthy_class_label,
        cancer_class_labels=anchors.cancer_class_labels,
        all_class_labels=class_names,
        anchor_strategy=anchors.anchor_strategy,
        expected_feature_order_fingerprint=anchors.feature_order_fingerprint,
        centroid_dir_by_class_label=centroid_dirs,
    )

    for label in class_names:
        name = f"weighted_cosine_distance_to_centroid__{label}"
        assert name in feat.feature_names
        assert name in feat.training_feature_names

    idx_h = feat.feature_names.index("weighted_cosine_distance_to_centroid__healthy")
    idx_p1 = feat.feature_names.index("weighted_cosine_distance_to_centroid__pca1")
    idx_p2 = feat.feature_names.index("weighted_cosine_distance_to_centroid__pca2")

    healthy_dist = float(feat.X[0, idx_h])
    pca1_dist = float(feat.X[0, idx_p1])
    pca2_dist = float(feat.X[0, idx_p2])
    assert np.isfinite(healthy_dist)
    assert healthy_dist <= pca1_dist + 1e-9
    assert healthy_dist <= pca2_dist + 1e-9
    assert healthy_dist >= -1e-6
    assert healthy_dist <= 2.0

    cancer_like_dist = float(feat.X[2, idx_p1])
    assert np.isfinite(cancer_like_dist)
    assert cancer_like_dist < float(feat.X[2, idx_h])


def test_observed_feature_builder_centroid_distance_missing_dir_is_nan(monkeypatch):
    monkeypatch.setattr(
        observed_feature_builder.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract_complete,
    )
    sample_paths = ["/tmp/S1", "/tmp/S2", "/tmp/S3", "/tmp/S4"]
    y = [0, 0, 1, 1]
    class_names = ["healthy", "cancer"]
    dmp_df = _dmp_df()
    anchors = _derive_anchors(sample_paths, y, class_names, dmp_df)
    feat = observed_feature_builder.build_observed_hybrid_feature_table(
        sample_paths,
        dmp_df,
        healthy_reference_vector=anchors.healthy_reference_vector,
        cancer_reference_vector=anchors.cancer_reference_vector,
        per_cancer_reference_vectors=anchors.per_cancer_reference_vectors,
        healthy_class_label=anchors.healthy_class_label,
        cancer_class_labels=anchors.cancer_class_labels,
        all_class_labels=class_names,
        anchor_strategy=anchors.anchor_strategy,
        expected_feature_order_fingerprint=anchors.feature_order_fingerprint,
        centroid_dir_by_class_label={"healthy": "/tmp/healthy"},
    )
    idx_cancer = feat.feature_names.index("weighted_cosine_distance_to_centroid__cancer")
    idx_healthy = feat.feature_names.index("weighted_cosine_distance_to_centroid__healthy")
    assert np.isfinite(float(feat.X[0, idx_healthy]))
    assert not np.isfinite(float(feat.X[0, idx_cancer]))


def test_observed_feature_builder_histogram_tail_features_behaviors(monkeypatch):
    monkeypatch.setattr(
        observed_feature_builder.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract_complete,
    )

    def _fake_load_binned_counts(dmps_df, centroid1_dir, centroid2_dir, chromosome):
        del chromosome
        n = len(dmps_df)
        bin_edges = np.asarray([0.0, 0.5, 1.0], dtype=np.float64)
        healthy = np.zeros((n, 2), dtype=np.int32)
        cancer = np.zeros((n, 2), dtype=np.int32)
        healthy[:, 0] = 10
        healthy[:, 1] = 90
        if str(centroid2_dir).endswith("cancer"):
            cancer[:, 0] = 5
            cancer[:, 1] = 95
        else:
            cancer[:, 0] = 50
            cancer[:, 1] = 50
        return bin_edges, healthy, cancer

    monkeypatch.setattr(
        observed_feature_builder.MethylCentroidPair,
        "load_binned_counts_from_centroids",
        staticmethod(_fake_load_binned_counts),
    )
    sample_paths = ["/tmp/S1", "/tmp/S2", "/tmp/S3", "/tmp/S4"]
    y = [0, 0, 1, 1]
    class_names = ["healthy", "cancer"]
    dmp_df = _dmp_df()
    anchors = _derive_anchors(sample_paths, y, class_names, dmp_df)
    feat = observed_feature_builder.build_observed_hybrid_feature_table(
        sample_paths,
        dmp_df,
        healthy_reference_vector=anchors.healthy_reference_vector,
        cancer_reference_vector=anchors.cancer_reference_vector,
        per_cancer_reference_vectors=anchors.per_cancer_reference_vectors,
        healthy_class_label=anchors.healthy_class_label,
        cancer_class_labels=anchors.cancer_class_labels,
        anchor_strategy=anchors.anchor_strategy,
        expected_feature_order_fingerprint=anchors.feature_order_fingerprint,
        centroid_dir_by_class_label={"healthy": "/tmp/healthy", "cancer": "/tmp/cancer"},
        hist_eps=1e-6,
        hist_alpha=0.5,
        hist_evidence_clip_cap=5.0,
        hist_tail_agreement_threshold=0.10,
    )
    idx_e = feat.feature_names.index("weighted_healthy_tail_evidence__cancer")
    assert float(feat.X[0, idx_e]) < float(feat.X[2, idx_e])
    assert float(feat.X[2, idx_e]) <= 5.0


def test_observed_feature_builder_histogram_tail_features_nan_when_invalid(monkeypatch):
    monkeypatch.setattr(
        observed_feature_builder.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract_complete,
    )

    def _fake_load_zero_counts(dmps_df, centroid1_dir, centroid2_dir, chromosome):
        del centroid1_dir, centroid2_dir, chromosome
        n = len(dmps_df)
        bin_edges = np.asarray([0.0, 0.5, 1.0], dtype=np.float64)
        healthy = np.zeros((n, 2), dtype=np.int32)
        cancer = np.zeros((n, 2), dtype=np.int32)
        return bin_edges, healthy, cancer

    monkeypatch.setattr(
        observed_feature_builder.MethylCentroidPair,
        "load_binned_counts_from_centroids",
        staticmethod(_fake_load_zero_counts),
    )
    sample_paths = ["/tmp/S1", "/tmp/S2", "/tmp/S3", "/tmp/S4"]
    y = [0, 0, 1, 1]
    class_names = ["healthy", "cancer"]
    dmp_df = _dmp_df()
    anchors = _derive_anchors(sample_paths, y, class_names, dmp_df)
    feat = observed_feature_builder.build_observed_hybrid_feature_table(
        sample_paths,
        dmp_df,
        healthy_reference_vector=anchors.healthy_reference_vector,
        cancer_reference_vector=anchors.cancer_reference_vector,
        per_cancer_reference_vectors=anchors.per_cancer_reference_vectors,
        healthy_class_label=anchors.healthy_class_label,
        cancer_class_labels=anchors.cancer_class_labels,
        anchor_strategy=anchors.anchor_strategy,
        expected_feature_order_fingerprint=anchors.feature_order_fingerprint,
        centroid_dir_by_class_label={"healthy": "/tmp/healthy", "cancer": "/tmp/cancer"},
    )
    idx_e = feat.feature_names.index("weighted_healthy_tail_evidence__cancer")
    assert not np.isfinite(float(feat.X[0, idx_e]))


def _dmp_df_with_mapped_features() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "chromosome": ["1", "1", "1", "1"],
            "context": ["CG", "CG", "CG", "CG"],
            "position": [100, 120, 200, 220],
            "weight": [1.0, 2.0, 0.5, 0.7],
            "effect_size": [1.0, -2.0, 0.5, 0.7],
            "gene_name": ["G1", "G1", "G2", "unknown"],
            "feature_type": ["promoter", "exon", "intron", "promoter"],
        }
    )


def test_unfiltered_gene_and_structural_families_are_banned():
    for token in (
        "gene",
        "structural",
        "dmp_scored+gene",
        "dmp_scored+structural",
        "hybrid-all",
        "dmp+gene",
        "dmp+structural",
    ):
        with pytest.raises(ValueError, match="banned"):
            observed_feature_builder.normalize_feature_family_set(token)


def test_family_flags_gene_scored_tokens():
    assert observed_feature_builder._family_flags("gene_scored") == (False, False, False, True, False, False)
    assert observed_feature_builder._family_flags("dmp_scored+gene_scored") == (True, False, False, True, False, False)
    assert observed_feature_builder._family_flags("structural_scored") == (False, False, False, False, True, False)
    assert observed_feature_builder._family_flags("dmp_scored+structural_scored") == (True, False, False, False, True, False)
    assert observed_feature_builder._family_flags("chromosome") == (False, False, False, False, False, True)
    assert observed_feature_builder._family_flags("dmp_scored+chromosome") == (True, False, False, False, False, True)


def test_normalize_feature_family_set_rejects_legacy_aliases():
    assert observed_feature_builder.normalize_feature_family_set("dmp_scored") == "dmp_scored"
    assert (
        observed_feature_builder.normalize_feature_family_set("dmp_scored+gene_scored")
        == "dmp_scored+gene_scored"
    )
    import pytest
    for legacy in ("dmp", "dmp+gene_scored", "dmp+structural_scored"):
        with pytest.raises(ValueError, match="Unsupported feature_family_set"):
            observed_feature_builder.normalize_feature_family_set(legacy)
    for banned in ("dmp+gene", "dmp+structural", "gene", "structural", "hybrid-all"):
        with pytest.raises(ValueError, match="banned"):
            observed_feature_builder.normalize_feature_family_set(banned)


def test_coerce_saved_feature_family_set_migrates_legacy_tokens():
    assert observed_feature_builder.coerce_saved_feature_family_set("gene") == "gene_scored"
    assert observed_feature_builder.coerce_saved_feature_family_set("structural") == "structural_scored"
    assert observed_feature_builder.coerce_saved_feature_family_set("dmp") == "dmp_scored"
    assert (
        observed_feature_builder.coerce_saved_feature_family_set("dmp+gene_scored")
        == "dmp_scored+gene_scored"
    )
    assert (
        observed_feature_builder.coerce_saved_feature_family_set("dmp_scored+gene")
        == "dmp_scored+gene_scored"
    )
    assert observed_feature_builder.coerce_saved_feature_family_set("gene_scored") == "gene_scored"
    with pytest.raises(ValueError, match="banned"):
        observed_feature_builder.coerce_saved_feature_family_set("hybrid-all")


def test_gene_scored_feature_names_and_fingerprint():
    from methyl_validation.gene_scored_features import (
        compute_gene_scored_matrices,
        gene_directional_iqr_column,
        gene_panel_obs_fraction_column,
        gene_scored_feature_column,
        gene_weighted_sign_agreement_column,
        prepare_gene_scored_panels,
    )

    dmp_df = pd.DataFrame(
        {
            "comparison_label": ["cmp_a", "cmp_a", "cmp_a", "cmp_b", "cmp_b"],
            "chromosome": ["1", "1", "1", "1", "1"],
            "context": ["CG", "CG", "CG", "CG", "CG"],
            "position": [100, 120, 200, 100, 120],
            "effect_size": [1.0, -1.0, 0.5, 1.0, -1.0],
            "gene_name": ["G1", "G1", "G2", "G1", "G1"],
            "feature_type": ["promoter", "exon", "intron", "promoter", "exon"],
            "region_weight": [1.0, 1.0, 1.0, 1.0, 1.0],
        }
    )
    frozen_panel = pd.DataFrame(
        {
            "comparison_label": ["cmp_a", "cmp_a", "cmp_a"],
            "gene_name": ["G1", "G2", "G3"],
            "gene_support_n": [2, 2, 1],
            "gene_importance": [1.0, 0.5, 0.9],
            "gene_effect_signed_wsum": [1.0, -1.0, 0.5],
        }
    )
    names = observed_feature_builder.observed_hybrid_feature_names(
        feature_family_set="gene_scored",
        dmp_df=dmp_df,
        frozen_gene_panel_df=frozen_panel,
        cancer_class_labels=["pca_low"],
        all_class_labels=["all", "pca_low"],
    )
    assert names[:4] == [
        "gene_directional_score__cmp_a",
        "gene_panel_obs_fraction__cmp_a",
        "gene_directional_iqr__cmp_a",
        "gene_weighted_sign_agreement__cmp_a",
    ]
    assert "gene_max_weighted_directional_score" in names
    assert "gene_weighted_centroid_contrast_score" in names
    assert "gene_weighted_directional_agreement__pca_low" in names
    assert "gene_weighted_cosine_similarity_to_cancer_centroid__pca_low" in names
    assert "gene_weighted_cosine_distance_to_centroid__all" in names
    assert "gene_weighted_cosine_distance_to_centroid__pca_low" in names
    assert "gene::" not in names[0]

    # Without class labels, centroid analogs still appear with comparison-label suffixes
    names_default = observed_feature_builder.observed_hybrid_feature_names(
        feature_family_set="gene_scored",
        dmp_df=dmp_df,
        frozen_gene_panel_df=frozen_panel,
    )
    assert "gene_max_weighted_directional_score" in names_default
    assert "gene_weighted_directional_agreement__cmp_a" in names_default

    # No name collisions when both families enabled
    both = observed_feature_builder.observed_hybrid_feature_names(
        feature_family_set="dmp_scored+gene_scored",
        dmp_df=dmp_df,
        frozen_gene_panel_df=frozen_panel,
        cancer_class_labels=["pca_low"],
        all_class_labels=["all", "pca_low"],
    )
    assert len(both) == len(set(both))
    assert "max_weighted_directional_score" in both
    assert "gene_max_weighted_directional_score" in both

    fp_a = observed_feature_builder.observed_hybrid_schema_fingerprint(
        feature_family_set="gene_scored",
        dmp_df=dmp_df,
        frozen_gene_panel_df=frozen_panel,
        gene_scored_min_support_n=2,
    )
    fp_b = observed_feature_builder.observed_hybrid_schema_fingerprint(
        feature_family_set="gene_scored",
        dmp_df=dmp_df,
        frozen_gene_panel_df=frozen_panel,
        gene_scored_min_support_n=3,
    )
    fp_c = observed_feature_builder.observed_hybrid_schema_fingerprint(
        feature_family_set="gene_scored",
        dmp_df=dmp_df,
        frozen_gene_panel_df=frozen_panel,
        gene_scored_min_support_n=2,
        gene_scored_gene_weight="importance_only",
    )
    assert fp_a != fp_b
    assert fp_a != fp_c

    feature_order = [("1", "CG", 100), ("1", "CG", 120), ("1", "CG", 200)]
    X_raw = np.asarray([[0.10, 0.20, 0.30]], dtype=np.float64)
    panels = prepare_gene_scored_panels(frozen_panel, min_support_n=2)
    directional, obs_fraction, directional_iqr, sign_agreement = compute_gene_scored_matrices(
        X_raw,
        feature_order,
        dmp_df,
        panels,
        ["cmp_a"],
        use_region_weight=True,
        gene_weight_mode="importance_x_sqrt_support",
    )
    assert directional.shape == (1, 1)
    assert float(directional[0, 0]) == pytest.approx(-0.1, rel=1e-5, abs=1e-6)
    assert float(obs_fraction[0, 0]) == pytest.approx(1.0, rel=1e-5, abs=1e-6)
    assert float(directional_iqr[0, 0]) == pytest.approx(0.075, rel=1e-5, abs=1e-6)
    assert float(sign_agreement[0, 0]) == pytest.approx(1.0 / 3.0, rel=1e-5, abs=1e-6)
    assert gene_scored_feature_column("cmp_a") == "gene_directional_score__cmp_a"
    assert gene_panel_obs_fraction_column("cmp_a") == "gene_panel_obs_fraction__cmp_a"
    assert gene_directional_iqr_column("cmp_a") == "gene_directional_iqr__cmp_a"
    assert gene_weighted_sign_agreement_column("cmp_a") == "gene_weighted_sign_agreement__cmp_a"


def test_gene_scored_centroid_analog_features_finite():
    from methyl_validation.gene_scored_features import (
        GENE_MAX_WEIGHTED_DIRECTIONAL_SCORE,
        GENE_WEIGHTED_CENTROID_CONTRAST_SCORE,
        compute_gene_scored_centroid_features,
        gene_weighted_cosine_distance_to_centroid_column,
        gene_weighted_directional_agreement_column,
        prepare_gene_scored_panels,
    )

    dmp_df = pd.DataFrame(
        {
            "comparison_label": ["cmp_a", "cmp_a", "cmp_a"],
            "chromosome": ["1", "1", "1"],
            "context": ["CG", "CG", "CG"],
            "position": [100, 120, 200],
            "effect_size": [1.0, -1.0, 0.5],
            "gene_name": ["G1", "G1", "G2"],
            "feature_type": ["promoter", "exon", "intron"],
            "region_weight": [1.0, 1.0, 1.0],
        }
    )
    frozen_panel = pd.DataFrame(
        {
            "comparison_label": ["cmp_a", "cmp_a"],
            "gene_name": ["G1", "G2"],
            "gene_support_n": [2, 2],
            "gene_importance": [1.0, 0.5],
            "gene_effect_signed_wsum": [1.0, -1.0],
        }
    )
    feature_order = [("1", "CG", 100), ("1", "CG", 120), ("1", "CG", 200)]
    # Sample pushed toward cancer-like betas vs healthy/cancer refs
    X_raw = np.asarray([[0.8, 0.2, 0.7], [0.2, 0.8, 0.3]], dtype=np.float64)
    healthy = np.asarray([0.2, 0.8, 0.3], dtype=np.float64)
    cancer = np.asarray([0.8, 0.2, 0.7], dtype=np.float64)
    panels = prepare_gene_scored_panels(frozen_panel, min_support_n=2)
    feat = compute_gene_scored_centroid_features(
        X_raw,
        feature_order,
        dmp_df,
        panels,
        ["cmp_a"],
        healthy_reference_vector=healthy,
        cancer_reference_vector=cancer,
        per_cancer_reference_vectors=[cancer],
        cancer_class_labels=["pca_low"],
        all_class_labels=["all", "pca_low"],
        centroid_refs_by_label={"all": healthy, "pca_low": cancer},
    )
    assert GENE_MAX_WEIGHTED_DIRECTIONAL_SCORE in feat
    assert np.isfinite(feat[GENE_MAX_WEIGHTED_DIRECTIONAL_SCORE][0])
    assert np.isfinite(feat[GENE_WEIGHTED_CENTROID_CONTRAST_SCORE][0])
    assert np.isfinite(feat[gene_weighted_directional_agreement_column("pca_low")][0])
    # First sample matches cancer centroid -> smaller distance to pca_low than to all
    d_all = feat[gene_weighted_cosine_distance_to_centroid_column("all")]
    d_pca = feat[gene_weighted_cosine_distance_to_centroid_column("pca_low")]
    assert np.isfinite(d_all[0]) and np.isfinite(d_pca[0])
    assert float(d_pca[0]) < float(d_all[0])


def test_prepare_gene_scored_panels_raises_when_min_support_filters_all_rows():
    from methyl_validation.gene_scored_features import prepare_gene_scored_panels

    frozen_panel = pd.DataFrame(
        {
            "comparison_label": ["PCa", "PCa"],
            "gene_name": ["OR11H2", "OR4N2"],
            "gene_support_n": [2, 2],
            "gene_importance": [0.7, 0.7],
            "gene_effect_signed_wsum": [0.2, 0.2],
        }
    )
    with pytest.raises(ValueError, match="No genes passed gene_scored_min_support_n=5"):
        prepare_gene_scored_panels(frozen_panel, min_support_n=5)


def test_prepare_gene_scored_panels_returns_empty_when_input_empty():
    from methyl_validation.gene_scored_features import prepare_gene_scored_panels

    assert prepare_gene_scored_panels(pd.DataFrame(), min_support_n=5) == {}


def test_gene_weighted_sign_agreement_nan_without_prior():
    from methyl_validation.gene_scored_features import compute_gene_scored_matrices, prepare_gene_scored_panels

    dmp_df = pd.DataFrame(
        {
            "comparison_label": ["cmp_a"],
            "chromosome": ["1"],
            "context": ["CG"],
            "position": [100],
            "effect_size": [1.0],
            "gene_name": ["G1"],
        }
    )
    frozen_panel = pd.DataFrame(
        {
            "comparison_label": ["cmp_a"],
            "gene_name": ["G1"],
            "gene_support_n": [2],
            "gene_importance": [1.0],
            "gene_effect_signed_wsum": [0.0],
        }
    )
    feature_order = [("1", "CG", 100)]
    X_raw = np.asarray([[0.10]], dtype=np.float64)
    panels = prepare_gene_scored_panels(frozen_panel, min_support_n=2)
    _, _, _, sign_agreement = compute_gene_scored_matrices(
        X_raw,
        feature_order,
        dmp_df,
        panels,
        ["cmp_a"],
    )
    assert not np.isfinite(sign_agreement[0, 0])


def test_gene_scored_panel_obs_fraction_single_gene_observed():
    from methyl_validation.gene_scored_features import compute_gene_scored_matrices, prepare_gene_scored_panels

    dmp_df = pd.DataFrame(
        {
            "comparison_label": ["cmp_a", "cmp_a"],
            "chromosome": ["1", "1"],
            "context": ["CG", "CG"],
            "position": [100, 120],
            "effect_size": [1.0, -1.0],
            "gene_name": ["G1", "G1"],
        }
    )
    frozen_panel = pd.DataFrame(
        {
            "comparison_label": ["cmp_a", "cmp_a"],
            "gene_name": ["G1", "G2"],
            "gene_support_n": [2, 2],
            "gene_importance": [1.0, 1.0],
        }
    )
    feature_order = [("1", "CG", 100), ("1", "CG", 120)]
    X_raw = np.asarray([[0.10, 0.20]], dtype=np.float64)
    panels = prepare_gene_scored_panels(frozen_panel, min_support_n=2)
    _, obs_fraction, directional_iqr, _ = compute_gene_scored_matrices(
        X_raw,
        feature_order,
        dmp_df,
        panels,
        ["cmp_a"],
    )
    assert float(obs_fraction[0, 0]) == pytest.approx(0.5, rel=1e-5, abs=1e-6)
    assert not np.isfinite(directional_iqr[0, 0])


def test_compute_region_directional_score_matrix_hand_calculation():
    from methyl_validation.gene_scored_features import compute_region_directional_score_matrix

    dmp_df = pd.DataFrame(
        {
            "comparison_label": ["cmp_a", "cmp_a"],
            "chromosome": ["1", "1"],
            "context": ["CG", "CG"],
            "position": [100, 120],
            "effect_size": [1.0, -1.0],
            "feature_type": ["promoter", "promoter"],
            "region_weight": [1.0, 1.0],
        }
    )
    feature_order = [("1", "CG", 100), ("1", "CG", 120)]
    X_raw = np.asarray([[0.10, 0.20]], dtype=np.float64)
    matrix, specs = compute_region_directional_score_matrix(
        X_raw,
        feature_order,
        dmp_df,
        ["cmp_a"],
        ["promoter"],
        min_loci=1,
        use_region_weight=True,
    )
    assert specs == [("cmp_a", "promoter")]
    assert float(matrix[0, 0]) == pytest.approx(-0.05, rel=1e-5, abs=1e-6)


def _fake_extract_gene_scored(sample_paths, reference_positions, chromosome, min_coverage=1):
    del chromosome, min_coverage
    poss = []
    ctxs = []
    for ctx in sorted(reference_positions.keys()):
        for p in list(np.asarray(reference_positions[ctx], dtype=np.uint32)):
            ctxs.append(ctx)
            poss.append(int(p))
    rows = []
    for s in sample_paths:
        sid = Path(str(s)).name
        if sid in {"S1", "S2"}:
            vals = [0.10, 0.20, 0.30]
        else:
            vals = [0.70, 0.80, 0.90]
        rows.append(vals[: len(poss)])
    X = np.asarray(rows, dtype=np.float32)
    return (
        X,
        np.asarray(poss, dtype=np.uint32),
        np.asarray(ctxs, dtype=object),
        {"CG": np.arange(len(poss), dtype=np.uint32)},
    )


def test_observed_feature_builder_gene_scored_family(monkeypatch):
    monkeypatch.setattr(
        observed_feature_builder.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract_gene_scored,
    )
    dmp_df = pd.DataFrame(
        {
            "comparison_label": ["cmp_a", "cmp_a", "cmp_a"],
            "chromosome": ["1", "1", "1"],
            "context": ["CG", "CG", "CG"],
            "position": [100, 120, 200],
            "weight": [1.0, 1.0, 1.0],
            "effect_size": [1.0, -1.0, 0.5],
            "gene_name": ["G1", "G1", "G2"],
            "feature_type": ["promoter", "exon", "intron"],
        }
    )
    frozen_panel = pd.DataFrame(
        {
            "comparison_label": ["cmp_a", "cmp_a"],
            "gene_name": ["G1", "G2"],
            "gene_support_n": [2, 2],
            "gene_importance": [1.0, 0.5],
            "gene_effect_signed_wsum": [1.0, -1.0],
        }
    )
    sample_paths = ["/tmp/S1", "/tmp/S2", "/tmp/S3", "/tmp/S4"]
    y = [0, 0, 1, 1]
    class_names = ["healthy", "cancer"]
    anchors = _derive_anchors(sample_paths, y, class_names, dmp_df)
    feat = observed_feature_builder.build_observed_hybrid_feature_table(
        sample_paths,
        dmp_df,
        healthy_reference_vector=anchors.healthy_reference_vector,
        cancer_reference_vector=anchors.cancer_reference_vector,
        healthy_class_label=anchors.healthy_class_label,
        cancer_class_labels=anchors.cancer_class_labels,
        anchor_strategy=anchors.anchor_strategy,
        expected_feature_order_fingerprint=anchors.feature_order_fingerprint,
        feature_family_set="gene_scored",
        frozen_gene_panel_df=frozen_panel,
        gene_scored_min_support_n=2,
    )
    assert "gene_directional_score__cmp_a" in feat.feature_names
    assert "gene_panel_obs_fraction__cmp_a" in feat.feature_names
    assert "gene_directional_iqr__cmp_a" in feat.feature_names
    assert "gene_weighted_sign_agreement__cmp_a" in feat.feature_names
    assert not any(str(n).startswith("gene::") for n in feat.feature_names)
    assert not any(str(n).startswith("region_directional_score__") for n in feat.feature_names)
    gene_col = feat.feature_names.index("gene_directional_score__cmp_a")
    obs_col = feat.feature_names.index("gene_panel_obs_fraction__cmp_a")
    iqr_col = feat.feature_names.index("gene_directional_iqr__cmp_a")
    agree_col = feat.feature_names.index("gene_weighted_sign_agreement__cmp_a")
    assert float(feat.X[0, gene_col]) == pytest.approx(-0.1, rel=1e-5, abs=1e-6)
    assert float(feat.X[0, obs_col]) == pytest.approx(1.0, rel=1e-5, abs=1e-6)
    assert float(feat.X[0, iqr_col]) == pytest.approx(0.075, rel=1e-5, abs=1e-6)
    assert float(feat.X[0, agree_col]) == pytest.approx(1.0 / 3.0, rel=1e-5, abs=1e-6)
    assert feat.report["feature_families"]["gene_scored"] is True
    assert feat.report["feature_families"]["gene"] is False


def test_observed_feature_builder_dmp_plus_gene_scored_includes_both_families(monkeypatch):
    monkeypatch.setattr(
        observed_feature_builder.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract_gene_scored,
    )
    dmp_df = pd.DataFrame(
        {
            "comparison_label": ["cmp_a", "cmp_a", "cmp_a"],
            "chromosome": ["1", "1", "1"],
            "context": ["CG", "CG", "CG"],
            "position": [100, 120, 200],
            "weight": [1.0, 1.0, 1.0],
            "effect_size": [1.0, -1.0, 0.5],
            "gene_name": ["G1", "G1", "G2"],
            "feature_type": ["promoter", "exon", "intron"],
        }
    )
    frozen_panel = pd.DataFrame(
        {
            "comparison_label": ["cmp_a", "cmp_a"],
            "gene_name": ["G1", "G2"],
            "gene_support_n": [2, 2],
            "gene_importance": [1.0, 0.5],
        }
    )
    sample_paths = ["/tmp/S1", "/tmp/S2"]
    y = [0, 1]
    class_names = ["healthy", "cancer"]
    anchors = _derive_anchors(sample_paths, y, class_names, dmp_df)
    feat = observed_feature_builder.build_observed_hybrid_feature_table(
        sample_paths,
        dmp_df,
        healthy_reference_vector=anchors.healthy_reference_vector,
        cancer_reference_vector=anchors.cancer_reference_vector,
        healthy_class_label=anchors.healthy_class_label,
        cancer_class_labels=anchors.cancer_class_labels,
        anchor_strategy=anchors.anchor_strategy,
        expected_feature_order_fingerprint=anchors.feature_order_fingerprint,
        feature_family_set="dmp_scored+gene_scored",
        frozen_gene_panel_df=frozen_panel,
    )
    assert "max_weighted_directional_score" in feat.feature_names
    assert "gene_directional_score__cmp_a" in feat.feature_names
    assert "gene_weighted_sign_agreement__cmp_a" in feat.feature_names
    assert not any(str(n).startswith("gene::") for n in feat.feature_names)
    assert "weighted_mean_abs_distance_margin" not in feat.feature_names
    assert "obs_fraction" in feat.feature_names
    assert "obs_fraction" not in feat.training_feature_names
    assert len(feat.training_feature_names) + len(feat.quality_feature_names) == len(feat.feature_names)


def test_gene_scored_progression_k1_no_derived_columns():
    from methyl_validation.gene_scored_features import gene_scored_progression_feature_names

    assert gene_scored_progression_feature_names(["cmp_a"]) == []


def test_gene_scored_progression_k2_contrast_and_slope():
    from methyl_validation.gene_scored_features import (
        GENE_DIRECTIONAL_PROGRESSION_SLOPE,
        compute_gene_scored_progression_features,
        gene_directional_contrast_column,
        gene_scored_progression_feature_names,
    )

    labels = ["pca_low", "pca_high"]
    assert gene_scored_progression_feature_names(labels) == [
        gene_directional_contrast_column("pca_low", "pca_high"),
        GENE_DIRECTIONAL_PROGRESSION_SLOPE,
    ]
    mat = np.asarray([[0.1, 0.5]], dtype=np.float64)
    feats, _ = compute_gene_scored_progression_features(mat, labels)
    contrast_col = gene_directional_contrast_column("pca_low", "pca_high")
    assert float(feats[contrast_col][0]) == pytest.approx(0.4, rel=1e-5, abs=1e-6)
    assert float(feats[GENE_DIRECTIONAL_PROGRESSION_SLOPE][0]) == pytest.approx(0.4, rel=1e-5, abs=1e-6)


def test_gene_scored_progression_k4_column_count():
    from methyl_validation.gene_scored_features import (
        GENE_DIRECTIONAL_PROGRESSION_SLOPE,
        GENE_DIRECTIONAL_RANGE,
        compute_gene_scored_progression_features,
        gene_directional_adjacent_delta_column,
        gene_directional_contrast_column,
        gene_scored_progression_feature_names,
    )

    labels = ["stage_a", "stage_b", "stage_c", "stage_d"]
    names = gene_scored_progression_feature_names(labels)
    assert len(names) == 6
    assert names[0] == gene_directional_contrast_column("stage_a", "stage_d")
    assert GENE_DIRECTIONAL_PROGRESSION_SLOPE in names
    assert GENE_DIRECTIONAL_RANGE in names
    assert gene_directional_adjacent_delta_column("stage_a", "stage_b") in names
    mat = np.asarray([[0.0, 0.1, 0.3, 0.6]], dtype=np.float64)
    feats, _ = compute_gene_scored_progression_features(mat, labels)
    assert float(feats[GENE_DIRECTIONAL_RANGE][0]) == pytest.approx(0.6, rel=1e-5, abs=1e-6)
    assert float(feats[gene_directional_adjacent_delta_column("stage_b", "stage_c")][0]) == pytest.approx(
        0.2, rel=1e-5, abs=1e-6
    )


def test_gene_scored_progression_resolve_order_explicit_and_append_missing():
    from methyl_validation.gene_scored_features import resolve_gene_scored_progression_order

    order = resolve_gene_scored_progression_order(
        ["cmp_c", "cmp_a", "cmp_b"],
        explicit_order=["cmp_b", "cmp_a"],
    )
    assert order == ["cmp_b", "cmp_a", "cmp_c"]


def test_gene_scored_progression_nan_when_endpoint_missing():
    from methyl_validation.gene_scored_features import (
        GENE_DIRECTIONAL_PROGRESSION_SLOPE,
        compute_gene_scored_progression_features,
        gene_directional_contrast_column,
    )

    labels = ["pca_low", "pca_high"]
    mat = np.asarray([[0.1, np.nan]], dtype=np.float64)
    feats, _ = compute_gene_scored_progression_features(mat, labels)
    contrast_col = gene_directional_contrast_column("pca_low", "pca_high")
    assert not np.isfinite(feats[contrast_col][0])
    assert not np.isfinite(feats[GENE_DIRECTIONAL_PROGRESSION_SLOPE][0])


def test_gene_scored_progression_explicit_contrast_dedupes_extreme():
    from methyl_validation.gene_scored_features import (
        gene_directional_contrast_column,
        gene_scored_progression_feature_names,
    )

    labels = ["pca_low", "pca_high"]
    names = gene_scored_progression_feature_names(
        labels,
        contrast_pairs=[["pca_low", "pca_high"]],
    )
    contrast_col = gene_directional_contrast_column("pca_low", "pca_high")
    assert names.count(contrast_col) == 1


def test_gene_scored_progression_fingerprint_changes_with_order():
    dmp_df = pd.DataFrame(
        {
            "comparison_label": ["cmp_a", "cmp_b"],
            "chromosome": ["1", "1"],
            "context": ["CG", "CG"],
            "position": [100, 120],
            "effect_size": [1.0, -1.0],
            "gene_name": ["G1", "G1"],
        }
    )
    frozen_panel = pd.DataFrame(
        {
            "comparison_label": ["cmp_a", "cmp_b"],
            "gene_name": ["G1", "G1"],
            "gene_support_n": [2, 2],
            "gene_importance": [1.0, 1.0],
            "gene_effect_signed_wsum": [1.0, -1.0],
        }
    )
    fp_a = observed_feature_builder.observed_hybrid_schema_fingerprint(
        feature_family_set="gene_scored",
        dmp_df=dmp_df,
        frozen_gene_panel_df=frozen_panel,
        gene_scored_ordered_comparison_labels=["cmp_a", "cmp_b"],
    )
    fp_b = observed_feature_builder.observed_hybrid_schema_fingerprint(
        feature_family_set="gene_scored",
        dmp_df=dmp_df,
        frozen_gene_panel_df=frozen_panel,
        gene_scored_ordered_comparison_labels=["cmp_b", "cmp_a"],
    )
    assert fp_a != fp_b


def test_observed_feature_builder_gene_scored_progression_k2(monkeypatch):
    monkeypatch.setattr(
        observed_feature_builder.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract_gene_scored,
    )
    dmp_df = pd.DataFrame(
        {
            "comparison_label": ["pca_low", "pca_low", "pca_high", "pca_high"],
            "chromosome": ["1", "1", "1", "1"],
            "context": ["CG", "CG", "CG", "CG"],
            "position": [100, 120, 100, 120],
            "weight": [1.0, 1.0, 1.0, 1.0],
            "effect_size": [1.0, -1.0, 1.0, -1.0],
            "gene_name": ["G1", "G1", "G1", "G1"],
            "feature_type": ["promoter", "exon", "promoter", "exon"],
        }
    )
    frozen_panel = pd.DataFrame(
        {
            "comparison_label": ["pca_low", "pca_high"],
            "gene_name": ["G1", "G1"],
            "gene_support_n": [2, 2],
            "gene_importance": [1.0, 1.0],
            "gene_effect_signed_wsum": [1.0, -1.0],
        }
    )
    sample_paths = ["/tmp/S1", "/tmp/S2", "/tmp/S3", "/tmp/S4"]
    y = [0, 0, 1, 1]
    class_names = ["healthy", "cancer"]
    anchors = _derive_anchors(sample_paths, y, class_names, dmp_df)
    feat = observed_feature_builder.build_observed_hybrid_feature_table(
        sample_paths,
        dmp_df,
        healthy_reference_vector=anchors.healthy_reference_vector,
        cancer_reference_vector=anchors.cancer_reference_vector,
        healthy_class_label=anchors.healthy_class_label,
        cancer_class_labels=anchors.cancer_class_labels,
        anchor_strategy=anchors.anchor_strategy,
        expected_feature_order_fingerprint=anchors.feature_order_fingerprint,
        feature_family_set="gene_scored",
        frozen_gene_panel_df=frozen_panel,
        gene_scored_min_support_n=2,
        gene_scored_ordered_comparison_labels=["pca_low", "pca_high"],
    )
    from methyl_validation.gene_scored_features import (
        GENE_DIRECTIONAL_PROGRESSION_SLOPE,
        gene_directional_contrast_column,
    )

    contrast_col = gene_directional_contrast_column("pca_low", "pca_high")
    assert contrast_col in feat.feature_names
    assert GENE_DIRECTIONAL_PROGRESSION_SLOPE in feat.feature_names
    low_col = feat.feature_names.index("gene_directional_score__pca_low")
    high_col = feat.feature_names.index("gene_directional_score__pca_high")
    contrast_idx = feat.feature_names.index(contrast_col)
    assert float(feat.X[0, contrast_idx]) == pytest.approx(
        float(feat.X[0, high_col] - feat.X[0, low_col]), rel=1e-4, abs=1e-4
    )
    assert feat.report["gene_scored"]["progression_k"] == 2
    assert feat.report["gene_scored"]["progression_order"] == ["pca_low", "pca_high"]


def test_structural_scored_hand_calculation_and_dynamic_omission():
    from methyl_validation.structural_scored_features import (
        compute_structural_scored_matrices,
        prepare_structural_scored_panels,
        resolve_structural_scored_column_specs,
        structural_directional_score_column,
        structural_scored_feature_names,
    )

    dmp_df = pd.DataFrame(
        {
            "comparison_label": ["cmp_a", "cmp_a"],
            "chromosome": ["1", "1"],
            "context": ["CG", "CG"],
            "position": [100, 120],
            "effect_size": [1.0, -1.0],
            "gene_name": ["G1", "G1"],
            "feature_type": ["promoter", "promoter"],
            "region_weight": [1.0, 1.0],
        }
    )
    frozen_features = pd.DataFrame(
        {
            "comparison_label": ["cmp_a", "cmp_a"],
            "gene_name": ["G1", "G1"],
            "feature_type": ["promoter", "exon"],
            "n_dmps_in_feature": [2, 2],
            "feature_effect_compound": [1.0, 0.5],
        }
    )
    feature_order = [("1", "CG", 100), ("1", "CG", 120)]
    panels = prepare_structural_scored_panels(frozen_features, min_support_n=2)
    specs = resolve_structural_scored_column_specs(dmp_df, feature_order, panels, min_loci=1)
    assert specs == [("cmp_a", "promoter")]
    names = structural_scored_feature_names(specs, ["cmp_a"])
    assert structural_directional_score_column("cmp_a", "promoter") in names
    assert not any("__exon" in n for n in names)

    X_raw = np.asarray([[0.10, 0.20]], dtype=np.float64)
    directional, obs_fraction, directional_iqr, sign_agreement = compute_structural_scored_matrices(
        X_raw,
        feature_order,
        dmp_df,
        panels,
        specs,
        use_region_weight=True,
        weight_mode="compound_only",
    )
    assert float(directional[("cmp_a", "promoter")][0]) == pytest.approx(-0.05, rel=1e-5, abs=1e-6)
    assert float(obs_fraction[("cmp_a", "promoter")][0]) == pytest.approx(1.0, rel=1e-5, abs=1e-6)
    assert float(sign_agreement[("cmp_a", "promoter")][0]) == pytest.approx(0.0, rel=1e-5, abs=1e-6)
    assert not np.isfinite(float(directional_iqr[("cmp_a", "promoter")][0]))


def test_prepare_structural_scored_panels_raises_when_min_support_filters_all_rows():
    from methyl_validation.structural_scored_features import prepare_structural_scored_panels

    frozen_features = pd.DataFrame(
        {
            "comparison_label": ["cmp_a", "cmp_a"],
            "gene_name": ["G1", "G2"],
            "feature_type": ["promoter", "exon"],
            "n_dmps_in_feature": [2, 2],
            "feature_effect_compound": [1.0, 0.5],
        }
    )
    with pytest.raises(ValueError, match="No gene-feature rows passed structural_scored_min_support_n=5"):
        prepare_structural_scored_panels(frozen_features, min_support_n=5)


def test_structural_scored_gene_body_emitted_by_default():
    from methyl_validation.gene_scored_features import DEFAULT_REGION_DIRECTIONAL_TYPES
    from methyl_validation.structural_scored_features import (
        compute_structural_scored_partition_coverage,
        prepare_structural_scored_panels,
        resolve_structural_scored_column_specs,
    )

    assert "gene_body" in DEFAULT_REGION_DIRECTIONAL_TYPES
    dmp_df = pd.DataFrame(
        {
            "comparison_label": ["cmp_a"],
            "chromosome": ["1"],
            "context": ["CG"],
            "position": [100],
            "effect_size": [1.0],
            "gene_name": ["G1"],
            "feature_type": ["gene_body"],
            "region_weight": [1.0],
        }
    )
    frozen_features = pd.DataFrame(
        {
            "comparison_label": ["cmp_a"],
            "gene_name": ["G1"],
            "feature_type": ["gene_body"],
            "n_dmps_in_feature": [2],
            "feature_effect_compound": [1.0],
        }
    )
    feature_order = [("1", "CG", 100)]
    panels = prepare_structural_scored_panels(frozen_features, min_support_n=2)
    specs = resolve_structural_scored_column_specs(dmp_df, feature_order, panels, min_loci=1)
    assert ("cmp_a", "gene_body") in specs
    coverage = compute_structural_scored_partition_coverage(
        dmp_df,
        feature_order,
        panels,
        specs,
    )
    assert coverage["n_classifier_loci"] == 1
    assert coverage["n_loci_assigned_to_emitted_regions"] == 1
    assert coverage["partition_fraction"] == pytest.approx(1.0)


def test_structural_scored_progression_k2():
    from methyl_validation.structural_scored_features import (
        compute_structural_scored_progression_features,
        structural_directional_contrast_column,
        structural_directional_progression_slope_column,
    )

    directional_by_spec = {
        ("cmp_a", "promoter"): np.asarray([0.1, 0.2], dtype=np.float64),
        ("cmp_b", "promoter"): np.asarray([0.3, 0.4], dtype=np.float64),
    }
    specs = [("cmp_a", "promoter"), ("cmp_b", "promoter")]
    feats, names = compute_structural_scored_progression_features(
        directional_by_spec,
        specs,
        ["cmp_a", "cmp_b"],
    )
    contrast = structural_directional_contrast_column("cmp_a", "cmp_b", "promoter")
    slope = structural_directional_progression_slope_column("promoter")
    assert contrast in names
    assert slope in names
    assert float(feats[contrast][0]) == pytest.approx(0.2, rel=1e-5, abs=1e-6)


def test_observed_feature_builder_structural_scored_family(monkeypatch):
    monkeypatch.setattr(
        observed_feature_builder.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract_gene_scored,
    )
    dmp_df = pd.DataFrame(
        {
            "comparison_label": ["cmp_a", "cmp_a"],
            "chromosome": ["1", "1"],
            "context": ["CG", "CG"],
            "position": [100, 120],
            "effect_size": [1.0, -1.0],
            "gene_name": ["G1", "G1"],
            "feature_type": ["promoter", "promoter"],
            "region_weight": [1.0, 1.0],
        }
    )
    frozen_features = pd.DataFrame(
        {
            "comparison_label": ["cmp_a"],
            "gene_name": ["G1"],
            "feature_type": ["promoter"],
            "n_dmps_in_feature": [2],
            "feature_effect_compound": [1.0],
        }
    )
    anchors = observed_feature_builder.derive_observed_hybrid_anchors(
        ["S1", "S2"],
        [0, 1],
        ["healthy", "cancer"],
        dmp_df,
        feature_family_set="structural_scored",
        fixed_gene_features_df=frozen_features,
    )
    feat = observed_feature_builder.build_observed_hybrid_feature_table(
        ["S1", "S2"],
        dmp_df,
        healthy_reference_vector=anchors.healthy_reference_vector,
        cancer_reference_vector=anchors.cancer_reference_vector,
        per_cancer_reference_vectors=anchors.per_cancer_reference_vectors,
        healthy_class_label=anchors.healthy_class_label,
        cancer_class_labels=anchors.cancer_class_labels,
        all_class_labels=["healthy", "cancer"],
        anchor_strategy=anchors.anchor_strategy,
        expected_feature_order_fingerprint=anchors.feature_order_fingerprint,
        feature_family_set="structural_scored",
        fixed_gene_features_df=frozen_features,
        structural_scored_min_support_n=2,
        region_directional_min_loci=1,
    )
    assert feat.report["feature_families"]["structural_scored"] is True
    assert any(n.startswith("structural_directional_score__cmp_a__promoter") for n in feat.feature_names)
    assert not any("__exon" in n for n in feat.feature_names)
    assert not any(str(n).startswith("region_directional_score__") for n in feat.feature_names)


def test_preflight_structural_scored_training_fails_before_extraction():
    from methyl_validation.structural_scored_features import preflight_structural_scored_training

    dmp_df = pd.DataFrame(
        {
            "comparison_label": ["cmp_a"],
            "chromosome": ["1"],
            "context": ["CG"],
            "position": [100],
            "effect_size": [1.0],
            "gene_name": ["G1"],
            "feature_type": ["promoter"],
        }
    )
    frozen_features = pd.DataFrame(
        {
            "comparison_label": ["cmp_a"],
            "gene_name": ["G1"],
            "feature_type": ["promoter"],
            "n_dmps_in_feature": [2],
            "feature_effect_compound": [0.0],
        }
    )
    with pytest.raises(ValueError, match="zero training features"):
        preflight_structural_scored_training(
            dmp_df=dmp_df,
            feature_order=[("1", "CG", 100)],
            fixed_gene_features_df=frozen_features,
            feature_family_set="structural_scored",
        )


def test_require_structural_scored_columns_emitted_zero_compound():
    from methyl_validation.structural_scored_features import require_structural_scored_columns_emitted

    frozen_features = pd.DataFrame(
        {
            "comparison_label": ["cmp_a"],
            "gene_name": ["G1"],
            "feature_type": ["promoter"],
            "n_dmps_in_feature": [3],
            "feature_effect_compound": [0.0],
        }
    )
    with pytest.raises(ValueError, match="zero training features") as exc:
        require_structural_scored_columns_emitted(
            fixed_gene_features_df=frozen_features,
            panels={},
            column_specs=[],
            structural_scored_only=True,
        )
    assert "feature_effect_compound" in str(exc.value)


def test_require_structural_scored_columns_emitted_skipped_when_combined_family():
    from methyl_validation.structural_scored_features import require_structural_scored_columns_emitted

    require_structural_scored_columns_emitted(
        fixed_gene_features_df=pd.DataFrame(),
        panels={},
        column_specs=[],
        structural_scored_only=False,
    )


def test_build_observed_hybrid_structural_scored_only_raises_when_no_columns(monkeypatch):
    monkeypatch.setattr(
        observed_feature_builder.MethylCentroidPair,
        "extract_methylation_fractions",
        _fake_extract_gene_scored,
    )
    dmp_df = pd.DataFrame(
        {
            "comparison_label": ["cmp_a"],
            "chromosome": ["1"],
            "context": ["CG"],
            "position": [100],
            "effect_size": [1.0],
            "gene_name": ["G1"],
            "feature_type": ["promoter"],
        }
    )
    frozen_features = pd.DataFrame(
        {
            "comparison_label": ["cmp_a"],
            "gene_name": ["G1"],
            "feature_type": ["promoter"],
            "n_dmps_in_feature": [2],
            "feature_effect_compound": [0.0],
        }
    )
    anchors = observed_feature_builder.derive_observed_hybrid_anchors(
        ["S1", "S2"],
        [0, 1],
        ["healthy", "cancer"],
        dmp_df,
        feature_family_set="structural_scored",
        fixed_gene_features_df=frozen_features,
    )
    with pytest.raises(ValueError, match="zero training features"):
        observed_feature_builder.build_observed_hybrid_feature_table(
            ["S1", "S2"],
            dmp_df,
            healthy_reference_vector=anchors.healthy_reference_vector,
            cancer_reference_vector=anchors.cancer_reference_vector,
            per_cancer_reference_vectors=anchors.per_cancer_reference_vectors,
            healthy_class_label=anchors.healthy_class_label,
            cancer_class_labels=anchors.cancer_class_labels,
            all_class_labels=["healthy", "cancer"],
            anchor_strategy=anchors.anchor_strategy,
            expected_feature_order_fingerprint=anchors.feature_order_fingerprint,
            feature_family_set="structural_scored",
            fixed_gene_features_df=frozen_features,
        )
