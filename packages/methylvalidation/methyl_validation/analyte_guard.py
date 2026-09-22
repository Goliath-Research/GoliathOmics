"""
Training/inference analyte consistency checks (buffy_coat vs cfdna/plasma).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def normalize_analyte(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = "_".join(str(value).strip().lower().replace("-", " ").split())
    if cleaned in {"cfdna", "cf_dna", "cell_free_dna", "plasma", "plasma_cfdna"}:
        return "cfdna"
    if cleaned in {"buffy_coat", "buffy", "buffy_coat_dna", "wbmc"}:
        return "buffy_coat"
    if cleaned in {"combined", "mixed"}:
        return "combined"
    return cleaned or None


def effective_training_analyte(regulatory: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    Analyte used for stability/freeze/model training on this project.

    ``model_training_analyte`` overrides ``primary_analyte`` when set.
    """
    if not isinstance(regulatory, dict):
        return None
    explicit = normalize_analyte(regulatory.get("model_training_analyte"))
    if explicit:
        return explicit
    return normalize_analyte(regulatory.get("primary_analyte"))


def read_locked_model_analyte(production_dir: Path) -> Optional[str]:
    spec_path = production_dir / "locked_model_spec.json"
    if not spec_path.is_file():
        return None
    try:
        with open(spec_path, encoding="utf-8") as f:
            spec = json.load(f)
        reg = (spec.get("regulatory") or {}) if isinstance(spec, dict) else {}
        return effective_training_analyte(reg if isinstance(reg, dict) else None)
    except Exception:
        return None


def check_training_analyte_match(
    *,
    project_json: Path,
    production_dir: Optional[Path] = None,
) -> Tuple[bool, str]:
    """
    Return (ok, message). Compares current project regulatory analyte to locked model spec.
    """
    try:
        from methyl_utils import load_project
    except Exception as exc:
        return True, f"analyte check skipped: {exc}"

    try:
        project = load_project(project_json)
    except Exception as exc:
        # Production freeze snapshots historically embedded step_config; skip rather than abort model.
        return True, f"analyte check skipped (project load failed): {exc}"
    reg = project.get_regulatory_config()
    current = effective_training_analyte(reg)
    if current is None:
        return True, "training analyte not declared on project (no enforcement target)."

    prod_dir = production_dir
    if prod_dir is None:
        root = Path(project.get_project_root())
        prod_dir = root / "monte_carlo_runs" / "production"
    locked = read_locked_model_analyte(prod_dir)
    if locked is None:
        return True, (
            f"project training analyte={current!r}; no locked_model_spec.json yet "
            "(first model build on this cohort)."
        )

    if locked == current:
        return True, f"training analyte matches locked model ({current!r})."

    if locked == "combined" or current == "combined":
        return True, f"training analyte partial match (locked={locked!r}, project={current!r})."

    return False, (
        f"training analyte mismatch: project declares {current!r} but locked model was "
        f"trained for {locked!r}. Run full stability/freeze/--model on a {current!r} cohort "
        f"or use a buffy-coat-trained model only on buffy-coat data."
    )


def assert_training_analyte_match_for_model(
    project_json: Path,
    *,
    enforce: bool,
    production_dir: Optional[Path] = None,
) -> None:
    ok, msg = check_training_analyte_match(
        project_json=project_json,
        production_dir=production_dir,
    )
    if ok:
        print(f"[INFO] Analyte guard: {msg}")
        return
    if enforce:
        raise ValueError(msg)
    print(f"[WARN] Analyte guard: {msg}", flush=True)
