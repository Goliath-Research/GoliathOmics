"""
Prediction-branch helpers (dual DMP design).

Discovery lists (broad DMP CSVs for MethylMapper / MethylEnricher) and prediction panels
(classifier DMPs chosen for out-of-sample utility) are intentionally separated in
``methyldetector`` when ``dmp_export_mode=dual``.

Monte Carlo validation (``methyl_validation.project_gen``) already rebuilds centroids from
training samples each outer iteration, so DMP detection on those centroids avoids test-sample
leakage at the cohort level. Choosing *k* via ``classifier_dmp_selection=featurecuts_validation``
optimizes balanced accuracy on held-out validation *samples* configured on the project
(repeated holdouts inside ``MethylDetector``), which is stronger than elbow-only trimming but
is **not** a full nested cross-validation over independent centroid rebuilds per inner fold.

For strict nested CV (inner folds that each rebuild centroids and re-run detection), run
separate centroid/detector jobs per inner split or extend this package with a dedicated
orchestrator.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def detection_step_overrides_featurecuts(
    *,
    exhaustive: bool = False,
    max_candidates: Optional[int] = 50,
    max_k_cap: Optional[int] = 5000,
    dmp_export_mode: str = "dual",
) -> Dict[str, Any]:
    """
    Merge this dict into ``step_config.detection`` to enable validation-driven top-*k*
    selection (FeatureCuts) for the classifier panel while keeping dual CSV exports.

    Requires real validation samples (``centroid*_validation_samples`` or metadata paths).
    """
    out: Dict[str, Any] = {
        "dmp_export_mode": dmp_export_mode,
        "classifier_dmp_selection": "featurecuts_validation",
        "featurecuts_exhaustive_search": exhaustive,
        "featurecuts_max_k_cap": max_k_cap,
    }
    if max_candidates is not None:
        out["featurecuts_max_candidates"] = max_candidates
    return out


def detection_step_overrides_unified_legacy() -> Dict[str, Any]:
    """Single-file export matching pre–dual-branch behavior (mapper + classifier same elbow table)."""
    return {
        "dmp_export_mode": "unified",
        "classifier_dmp_selection": "elbow",
    }
