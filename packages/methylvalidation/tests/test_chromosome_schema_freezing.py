"""Train/predict schema freezing for chromosome-family metadata."""

from __future__ import annotations

import json
from pathlib import Path


def test_tabular_meta_persists_chromosome_tunables(tmp_path: Path) -> None:
    meta = {
        "feature_family_set": "dmp_scored+chromosome",
        "chromosome_hypo_beta_threshold": 0.2,
        "chromosome_intermediate_beta_lo": 0.25,
        "chromosome_intermediate_beta_hi": 0.75,
        "chromosome_distance_metrics": ["js", "hellinger"],
        "chromosome_list": ["1", "2"],
        "class_centroid_dirs": {"healthy": "/tmp/h", "disease": "/tmp/d"},
        "observed_feature_order_fingerprint": "abc123",
    }
    path = tmp_path / "tabular-model-metadata.json"
    path.write_text(json.dumps(meta), encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["feature_family_set"] == "dmp_scored+chromosome"
    assert loaded["chromosome_distance_metrics"] == ["js", "hellinger"]
    assert loaded["class_centroid_dirs"]["healthy"] == "/tmp/h"
    assert loaded["observed_feature_order_fingerprint"] == "abc123"
