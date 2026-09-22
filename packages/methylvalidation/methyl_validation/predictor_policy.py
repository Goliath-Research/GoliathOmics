"""
Rules for which predictor modes are allowed under methyl-validation.
"""

from typing import Any, Dict


def monte_carlo_rejects_blind_predictor(step_cfg: Dict[str, Any]) -> bool:
    """True if step_config.predictor is blind-only (not supported for Monte Carlo)."""
    if not step_cfg:
        return False
    blind = step_cfg.get("blind")
    if isinstance(blind, dict):
        groups = blind.get("groups")
        if isinstance(groups, list) and len(groups) > 0:
            return True
    tbp = step_cfg.get("test_blind_paths")
    if isinstance(tbp, list) and len(tbp) > 0:
        return True
    return False


def assert_monte_carlo_predictor_allowed(step_cfg: Dict[str, Any]) -> None:
    """
    Raise ValueError if Monte Carlo cannot run with this predictor config.
    Blind-only predictor is intentionally unsupported (use methyl-predictor standalone).
    """
    if monte_carlo_rejects_blind_predictor(step_cfg):
        raise ValueError(
            "methyl-validation does not support blind-only predictor (step_config.predictor.blind / "
            "test_blind_paths). Use labeled validation cohorts or run blind prediction outside MC."
        )


def assert_validation_predictor_accuracy_mode(step_cfg: Dict[str, Any]) -> None:
    """
    Raise ValueError when predictor is configured as blind-only for methyl-validation
    quality workflows where labeled metrics are required.
    """
    if monte_carlo_rejects_blind_predictor(step_cfg):
        raise ValueError(
            "methyl-validation quality workflows require labeled test/holdout cohorts to compute "
            "balanced accuracy and related metrics. Blind-only predictor mode "
            "(step_config.predictor.blind / test_blind_paths) is for final inference only."
        )
