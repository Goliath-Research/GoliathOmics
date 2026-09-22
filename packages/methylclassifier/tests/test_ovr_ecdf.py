"""OvR ECDF: union DMP construction, fusion, MethylClassifier load + predict_proba."""

import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from methyl_classifier.core.multiclass_ovr import (
    OvrMultiChromBinaryExpert,
    build_union_dmp_dataframe,
    fuse_ovr_binary_probas,
)
from methyl_classifier.core.classifier import MethylClassifier
from methyl_classifier.models.config import ClassifierConfig


def _write_min_detector_pkl(path: Path, position: int, chrom: str = "1") -> None:
    ecdf = _tiny_ecdf(np.array([position], dtype=np.uint32))
    df = pd.DataFrame({"chromosome": [chrom], "position": [int(position)]})
    pkg = {"classifier": ecdf, "dmpDF": df, "metadata": {}}
    with open(path, "wb") as f:
        pickle.dump(pkg, f, protocol=pickle.HIGHEST_PROTOCOL)
from methyl_classifier.utils.ovr_bundle import build_ecdf_ovr_package


def _tiny_ecdf(positions: np.ndarray) -> "ECDFClassifier":
    from methyl_utils.ecdf_classifier import ECDFClassifier

    n_dmps = int(positions.shape[0])
    n_bins = 4
    bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    bin_counts_c1 = np.ones((n_dmps, n_bins), dtype=np.float64)
    bin_counts_c2 = np.ones((n_dmps, n_bins), dtype=np.float64) * 1.5
    weights = np.ones(n_dmps, dtype=np.float64)
    directions = np.ones(n_dmps, dtype=np.int8)
    return ECDFClassifier(
        positions, bin_edges, bin_counts_c1, bin_counts_c2, weights, directions
    )


def test_build_union_dmp_overlapping_columns():
    """Three binaries with overlap: union width and per-model column maps."""
    e0 = _tiny_ecdf(np.array([100], dtype=np.uint32))
    e1 = _tiny_ecdf(np.array([100, 200], dtype=np.uint32))
    e2 = _tiny_ecdf(np.array([50], dtype=np.uint32))
    entries = [
        {
            "ecdf": e0,
            "dmp_df": pd.DataFrame({"chromosome": ["1"], "position": [100]}),
        },
        {
            "ecdf": e1,
            "dmp_df": pd.DataFrame({"chromosome": ["1", "1"], "position": [100, 200]}),
        },
        {
            "ecdf": e2,
            "dmp_df": pd.DataFrame({"chromosome": ["2"], "position": [50]}),
        },
    ]
    union_df, col_idx = build_union_dmp_dataframe(entries)
    assert len(union_df) == 3
    assert [int(x) for x in col_idx[0]] == [0]
    assert [int(x) for x in col_idx[1]] == [0, 1]
    assert [int(x) for x in col_idx[2]] == [2]


def test_fuse_ovr_binary_probas_rows_sum_to_one():
    n = 5
    p1 = np.tile([0.2, 0.8], (n, 1))
    p2 = np.tile([0.6, 0.4], (n, 1))
    p3 = np.tile([0.5, 0.5], (n, 1))
    out = fuse_ovr_binary_probas([p1, p2, p3])
    assert out.shape == (n, 3)
    np.testing.assert_allclose(out.sum(axis=1), 1.0, rtol=1e-5)


def test_fuse_ovr_binary_probas_all_nan_heads_yields_uniform():
    """No evidence from any head → discrete 1/K (not arbitrary argmax on fake 0.5 logits)."""
    n = 4
    bad = np.full((n, 2), np.nan, dtype=np.float64)
    out = fuse_ovr_binary_probas([bad, bad, bad])
    assert out.shape == (n, 3)
    np.testing.assert_allclose(out, 1.0 / 3.0, rtol=1e-10)
    np.testing.assert_allclose(out.sum(axis=1), 1.0, rtol=1e-10)


def test_fuse_ovr_binary_probas_nan_head_reweights_remaining():
    """Missing head is dropped from softmax; mass split among heads with finite logits."""
    n = 2
    p0 = np.tile([0.2, 0.8], (n, 1))
    p_missing = np.full((n, 2), np.nan, dtype=np.float64)
    p2 = np.tile([0.2, 0.8], (n, 1))
    out = fuse_ovr_binary_probas([p0, p_missing, p2])
    np.testing.assert_allclose(out[:, 1], 0.0, atol=1e-10)
    np.testing.assert_allclose(out[:, 0], 0.5, rtol=1e-10)
    np.testing.assert_allclose(out[:, 2], 0.5, rtol=1e-10)


def test_fuse_pairwise_max_contrast_avoids_aggregate_softmax_with_pairwises():
    """
    With ``control_pairwise_geometric``, the aggregate head repeats the same P(control) signal
    already in each pairwise; flat softmax inflates class 0. Max-contrast derives the control
    logit from the strongest pairwise disease-vs-control log-odds only.
    """
    n = 2
    agg = np.tile([0.2, 0.8], (n, 1))  # [P_not, P_ctrl] as emitted by OvrPairwiseControlAggregateExpert
    dis = np.tile([0.3, 0.7], (n, 1))  # [P_ctrl, P_dis] per pairwise detector
    flat = fuse_ovr_binary_probas([agg, dis, dis], pairwise_max_contrast_control=False)
    pmc = fuse_ovr_binary_probas([agg, dis, dis], pairwise_max_contrast_control=True)
    assert int(np.argmax(flat[0])) == 0
    assert int(np.argmax(pmc[0])) in (1, 2)
    np.testing.assert_allclose(flat.sum(axis=1), 1.0, rtol=1e-6)
    np.testing.assert_allclose(pmc.sum(axis=1), 1.0, rtol=1e-6)


def test_methyl_classifier_ovr_pkl_predict_proba_shape(tmp_path, monkeypatch, capsys):
    """Load ecdf_one_vs_rest PKL: (n, K) probas, approximately stochastic rows."""
    monkeypatch.setattr("methyl_classifier.core.classifier.sys.exit", lambda *_: pytest.fail("sys.exit"))

    entries = [
        {
            "ecdf": _tiny_ecdf(np.array([10], dtype=np.uint32)),
            "dmp_df": pd.DataFrame({"chromosome": ["1"], "position": [10]}),
        },
        {
            "ecdf": _tiny_ecdf(np.array([20], dtype=np.uint32)),
            "dmp_df": pd.DataFrame({"chromosome": ["1"], "position": [20]}),
        },
        {
            "ecdf": _tiny_ecdf(np.array([10], dtype=np.uint32)),
            "dmp_df": pd.DataFrame({"chromosome": ["2"], "position": [10]}),
        },
    ]
    pkg = build_ecdf_ovr_package(entries, ["c0", "c1", "c2"])
    pkl_path = tmp_path / "ovr.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump(pkg, f, protocol=pickle.HIGHEST_PROTOCOL)

    clf = MethylClassifier(ClassifierConfig(model_path=str(pkl_path)))
    assert clf._ovr_mode and clf.n_classes == 3
    assert len(clf.dmp_positions_df) == 3  # (1,10), (1,20), (2,10)

    X = np.array([[0.5, 0.5, 0.5]], dtype=np.float64)
    m = np.ones((1, 3), dtype=bool)
    proba = clf.predict_proba(X, m)
    assert proba.shape == (1, 3)
    np.testing.assert_allclose(proba.sum(axis=1), 1.0, rtol=1e-4)


def test_methyl_classifier_ovr_save_writes_ecdf_one_vs_rest_dict(tmp_path, monkeypatch):
    """Saving OvR MethylClassifier writes portable dict PKL reloadable via load_classifier."""
    monkeypatch.setattr("methyl_classifier.core.classifier.sys.exit", lambda *_: pytest.fail("sys.exit"))

    entries = [
        {
            "ecdf": _tiny_ecdf(np.array([10], dtype=np.uint32)),
            "dmp_df": pd.DataFrame({"chromosome": ["1"], "position": [10]}),
        },
        {
            "ecdf": _tiny_ecdf(np.array([20], dtype=np.uint32)),
            "dmp_df": pd.DataFrame({"chromosome": ["1"], "position": [20]}),
        },
    ]
    pkg = build_ecdf_ovr_package(entries, ["a", "b"])
    pkl_path = tmp_path / "ovr_in.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump(pkg, f, protocol=pickle.HIGHEST_PROTOCOL)

    clf = MethylClassifier(ClassifierConfig(model_path=str(pkl_path)))
    out_path = tmp_path / "saved.pkl"
    clf.save(out_path)
    with open(out_path, "rb") as f:
        dumped = pickle.load(f)
    assert isinstance(dumped, dict)
    assert dumped.get("classifier_type") == "ecdf_one_vs_rest"
    clf2 = MethylClassifier(ClassifierConfig(model_path=str(out_path)))
    assert clf2._ovr_mode and clf2.n_classes == 2


def test_classifier_config_ovr_binary_paths_assembles_without_model_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("methyl_classifier.core.classifier.sys.exit", lambda *_: pytest.fail("sys.exit"))
    p1 = tmp_path / "c0.pkl"
    p2 = tmp_path / "c1.pkl"
    _write_min_detector_pkl(p1, 100)
    _write_min_detector_pkl(p2, 200)
    clf = MethylClassifier(
        ClassifierConfig(
            ovr_binary_model_paths=[str(p1), str(p2)],
            ovr_class_names=["c0", "c1"],
        )
    )
    assert clf._ovr_mode and clf.n_classes == 2
    assert len(clf.dmp_positions_df) == 2


def test_export_ovr_pkl_helpers_match_cli_bundle(tmp_path, monkeypatch):
    """CLI export path helpers produce the same portable PKL as an OvR save."""
    monkeypatch.setattr("methyl_classifier.core.classifier.sys.exit", lambda *_: pytest.fail("sys.exit"))
    from methyl_classifier.cli.main import (
        _export_ovr_pkl_from_config,
        _resolve_ovr_export_output_path,
    )
    from methyl_classifier.models.config_schema import ClassificationConfig

    p0 = tmp_path / "b0.pkl"
    p1 = tmp_path / "b1.pkl"
    _write_min_detector_pkl(p0, 100)
    _write_min_detector_pkl(p1, 200)

    cfg = ClassificationConfig(
        ovr_binary_model_paths=[str(p0), str(p1)],
        ovr_class_names=["c0", "c1"],
        save_classifier_path=str(tmp_path / "from_config.pkl"),
    )
    assert _resolve_ovr_export_output_path(cfg, None) == tmp_path / "from_config.pkl"
    assert _resolve_ovr_export_output_path(cfg, str(tmp_path / "explicit.pkl")) == tmp_path / "explicit.pkl"

    _export_ovr_pkl_from_config(cfg, tmp_path / "from_config.pkl")
    with open(tmp_path / "from_config.pkl", "rb") as f:
        dumped = pickle.load(f)
    assert isinstance(dumped, dict)
    assert dumped.get("classifier_type") == "ecdf_one_vs_rest"
    clf2 = MethylClassifier(ClassifierConfig(model_path=str(tmp_path / "from_config.pkl")))
    assert clf2._ovr_mode and clf2.n_classes == 2


def test_default_ovr_unified_classifier_basename_matches_project_contexts(tmp_path):
    """CG-only project → classifier-1-CG.pkl probe; multi-context → comma-sorted join."""
    import json
    from methyl_utils import load_project

    from methyl_classifier.project_resolver import default_ovr_unified_classifier_basename

    p = tmp_path / "p.json"
    p.write_text(
        json.dumps(
            {
                "project_name": "t",
                "output_base": str(tmp_path),
                "samples_base_path": str(tmp_path),
                "controls": {"label": "h", "groups": [{"label": "c", "sample_paths": []}]},
                "diseases": {"label": "d", "groups": [{"label": "x", "sample_paths": []}]},
                "comparisons": [{"control_group": "c", "disease_group": "x"}],
                "chromosomes": ["1", "2"],
                "contexts": ["CG"],
            }
        ),
        encoding="utf-8",
    )
    proj = load_project(p)
    assert default_ovr_unified_classifier_basename(proj) == "classifier-1-CG.pkl"

    p2 = tmp_path / "p2.json"
    p2.write_text(
        json.dumps(
            {
                "project_name": "t2",
                "output_base": str(tmp_path),
                "samples_base_path": str(tmp_path),
                "controls": {"label": "h", "groups": [{"label": "c", "sample_paths": []}]},
                "diseases": {"label": "d", "groups": [{"label": "x", "sample_paths": []}]},
                "comparisons": [{"control_group": "c", "disease_group": "x"}],
                "chromosomes": ["2"],
                "contexts": ["CHH", "CG"],
            }
        ),
        encoding="utf-8",
    )
    proj2 = load_project(p2)
    assert default_ovr_unified_classifier_basename(proj2) == "classifier-2-CG,CHH.pkl"


def test_classifier_step_dict_has_ovr_sources():
    from methyl_classifier.project_resolver import classifier_step_dict_has_ovr_sources

    assert not classifier_step_dict_has_ovr_sources({})
    assert not classifier_step_dict_has_ovr_sources({"ovr_binary_model_paths": ["only_one"]})
    assert classifier_step_dict_has_ovr_sources({"ovr_binary_model_paths": ["a", "b"]})
    assert classifier_step_dict_has_ovr_sources(
        {"ovr_detection_dirs": ["/x", "/y"]}
    )
    assert classifier_step_dict_has_ovr_sources(
        {"ovr_binary_pickles_from_comparisons": True}
    )
    assert classifier_step_dict_has_ovr_sources(
        {"ovr_pairwise_aggregate_control": True, "ovr_detection_dirs": ["/one"]}
    )


def test_expand_ovr_paths_from_healthy_pca_project_comparisons():
    """Detection dirs follow detections/<control>/<disease>/ per comparison (all chromosomes)."""
    from pathlib import Path

    from methyl_utils import load_project

    from methyl_classifier.project_resolver import (
        expand_ovr_paths_from_comparisons,
        resolve_classifier_config,
    )

    repo = Path(__file__).resolve().parents[3]
    proj_path = repo / "configs" / "project_Healthy_vs_PCa1-4.json"
    if not proj_path.exists():
        pytest.skip("repo configs/project_Healthy_vs_PCa1-4.json not present")
    project = load_project(proj_path)
    bn = "classifier-1-CG,CHG,CHH.pkl"
    root = Path(project.get_project_root())
    dedicated = root / "detections" / "one_vs_rest" / "all" / bn
    resolved_labels = [x[0] for x in project.get_resolved_groups()]
    disease_labels = [lbl for lbl in resolved_labels if lbl != "all"]
    try:
        dirs, names, agg, bip = expand_ovr_paths_from_comparisons(project, unified_basename=bn)
        assert not bip
        assert names == resolved_labels
        if dedicated.is_file():
            assert not agg
            assert len(dirs) == len(resolved_labels)
            assert Path(dirs[0]) == dedicated.parent
            for dis in disease_labels:
                assert (root / "detections" / "all" / dis) == Path(dirs[names.index(dis)])
        else:
            assert agg
            assert len(dirs) == len(disease_labels)
            for dis in disease_labels:
                i = names.index(dis)
                assert (root / "detections" / "all" / dis) == Path(dirs[i - 1])

        cfg = resolve_classifier_config(proj_path)
        assert cfg.ovr_detection_dirs == dirs
        assert cfg.ovr_pairwise_aggregate_control == agg
        assert not (cfg.ovr_binary_model_paths or [])
        assert cfg.ovr_class_names == names
        assert cfg.save_classifier_path.replace("\\", "/").endswith(
            "/classifiers/all/classifier_all_Healthy_vs_PCa1-4.pkl"
        )
    except FileNotFoundError as e:
        pytest.skip(f"project sample paths or detector layout not available: {e}")


def test_ovr_multichrom_expert_reorders_columns_when_positions_differ_from_union_sort(
    monkeypatch,
):
    """Union DMP table is globally sorted; per-chrom ECDF may keep training row order."""
    monkeypatch.setattr("methyl_classifier.core.classifier.sys.exit", lambda *_: pytest.fail("sys.exit"))
    # Same three sites as ECDF, but dmp_df row order (and union slice order) is sorted;
    # ECDF positions follow unsorted training order [300, 100, 200].
    positions_train_order = np.array([300, 100, 200], dtype=np.uint32)
    ecdf = _tiny_ecdf(positions_train_order)
    union_df = pd.DataFrame(
        {
            "chromosome": pd.Categorical(["1", "1", "1"], categories=["1"]),
            "position": np.array([100, 200, 300], dtype=np.uint32),
        }
    )
    idx = np.array([0, 1, 2], dtype=np.intp)
    expert = OvrMultiChromBinaryExpert(
        {"1": ecdf},
        {"1": 1.0},
    )
    n = 2
    u = 3
    X = np.random.default_rng(0).random((n, u), dtype=np.float64)
    M = np.ones((n, u), dtype=bool)
    out = expert.predict_proba_binary(
        X, M, idx, union_df, calibrated=False, debug=False
    )
    assert out.shape == (n, 2)
    np.testing.assert_allclose(out.sum(axis=1), 1.0, rtol=1e-5)


def test_ovr_pairwise_aggregate_control_head(tmp_path, monkeypatch):
    """K-1 pairwise dirs + aggregate control: K distinct OvR heads, (n, K) probas."""
    monkeypatch.setattr("methyl_classifier.core.classifier.sys.exit", lambda *_: pytest.fail("sys.exit"))
    d1 = tmp_path / "all" / "d1"
    d2 = tmp_path / "all" / "d2"
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)
    _write_min_detector_pkl(d1 / "classifier-1-CG.pkl", 100, "1")
    _write_min_detector_pkl(d2 / "classifier-1-CG.pkl", 200, "1")
    clf = MethylClassifier(
        ClassifierConfig(
            ovr_detection_dirs=[str(d1), str(d2)],
            ovr_class_names=["ctrl", "d1", "d2"],
            ovr_pairwise_aggregate_control=True,
        )
    )
    assert clf._ovr_mode and clf.n_classes == 3
    assert len(clf._ovr_binary_classifiers) == 3
    from methyl_classifier.core.multiclass_ovr import OvrPairwiseControlAggregateExpert

    assert isinstance(clf._ovr_binary_classifiers[0], OvrPairwiseControlAggregateExpert)
    u = len(clf.dmp_positions_df)
    X = np.full((1, u), 0.5, dtype=np.float64)
    m = np.ones((1, u), dtype=bool)
    proba = clf.predict_proba(X, m)
    assert proba.shape == (1, 3)
    np.testing.assert_allclose(proba.sum(axis=1), 1.0, rtol=1e-4)


def test_ovr_bipartite_two_controls_two_diseases(tmp_path, monkeypatch):
    """2×2 detection dirs + bipartite aggregate → K=4 OvR heads."""
    monkeypatch.setattr("methyl_classifier.core.classifier.sys.exit", lambda *_: pytest.fail("sys.exit"))
    dirs = []
    for c in ("c1", "c2"):
        for d in ("d1", "d2"):
            p = tmp_path / c / d
            p.mkdir(parents=True)
            _write_min_detector_pkl(p / "classifier-1-CG.pkl", 50, "1")
            dirs.append(str(p))
    clf = MethylClassifier(
        ClassifierConfig(
            ovr_detection_dirs=dirs,
            ovr_class_names=["c1", "c2", "d1", "d2"],
            ovr_bipartite_aggregate=True,
            ovr_n_control_classes=2,
        )
    )
    assert clf.n_classes == 4
    assert len(clf._ovr_binary_classifiers) == 4
    u = len(clf.dmp_positions_df)
    X = np.full((1, u), 0.5, dtype=np.float64)
    m = np.ones((1, u), dtype=bool)
    proba = clf.predict_proba(X, m)
    assert proba.shape == (1, 4)
    np.testing.assert_allclose(proba.sum(axis=1), 1.0, rtol=1e-4)


def test_ovr_detection_dirs_multichrom_two_chromosomes(tmp_path, monkeypatch):
    """Two OvR classes, each dir has two chromosome pickles → union DMPs and (n, K) probas."""
    monkeypatch.setattr("methyl_classifier.core.classifier.sys.exit", lambda *_: pytest.fail("sys.exit"))
    d0 = tmp_path / "c0"
    d1 = tmp_path / "c1"
    d0.mkdir()
    d1.mkdir()
    _write_min_detector_pkl(d0 / "classifier-1-CG.pkl", 10, "1")
    _write_min_detector_pkl(d0 / "classifier-2-CG.pkl", 20, "2")
    _write_min_detector_pkl(d1 / "classifier-1-CG.pkl", 11, "1")
    _write_min_detector_pkl(d1 / "classifier-2-CG.pkl", 21, "2")
    clf = MethylClassifier(
        ClassifierConfig(
            ovr_detection_dirs=[str(d0), str(d1)],
            ovr_class_names=["a", "b"],
        )
    )
    assert clf._ovr_mode and clf.n_classes == 2
    assert len(clf.dmp_positions_df) == 4
    X = np.array([[0.5, 0.5, 0.5, 0.5]], dtype=np.float64)
    m = np.ones((1, 4), dtype=bool)
    proba = clf.predict_proba(X, m)
    assert proba.shape == (1, 2)
    np.testing.assert_allclose(proba.sum(axis=1), 1.0, rtol=1e-4)
