"""
Keys under ``step_config.detection`` that apply only to native multiclass PKL export
(``build_multiclass_model``), not to MethylDetector runtime. They are stripped before
building ``MethylDetectorConfig``.
"""

from __future__ import annotations

from typing import Any, Dict

DETECTION_MULTICLASS_EXPORT_KEYS = frozenset(
    {
        "multiclass_train_learned_head",
        "multiclass_learned_logistic_C",
        "multiclass_learned_max_iter",
        "multiclass_learned_standardize",
        "multiclass_learned_random_state",
        "multiclass_learned_class_weight",
    }
)


def filter_detection_config_for_detector(detection_step: Dict[str, Any]) -> Dict[str, Any]:
    if not detection_step:
        return {}
    return {
        k: v
        for k, v in detection_step.items()
        if k not in DETECTION_MULTICLASS_EXPORT_KEYS
    }


def multiclass_build_overrides_from_detection_step(
    detection_step: Dict[str, Any],
) -> Dict[str, Any]:
    """Map project ``step_config.detection`` keys to ``build_multiclass_model`` kwargs."""
    if not detection_step:
        return {}
    out: Dict[str, Any] = {}
    if bool(detection_step.get("multiclass_train_learned_head", False)):
        out["train_learned_multiclass"] = True
    if "multiclass_learned_logistic_C" in detection_step:
        out["learned_logistic_C"] = float(detection_step["multiclass_learned_logistic_C"])
    if "multiclass_learned_max_iter" in detection_step:
        out["learned_max_iter"] = int(detection_step["multiclass_learned_max_iter"])
    if "multiclass_learned_standardize" in detection_step:
        out["learned_standardize"] = bool(detection_step["multiclass_learned_standardize"])
    if "multiclass_learned_random_state" in detection_step:
        out["learned_random_state"] = int(detection_step["multiclass_learned_random_state"])
    if "multiclass_learned_class_weight" in detection_step:
        out["learned_class_weight"] = detection_step["multiclass_learned_class_weight"]
    return out
