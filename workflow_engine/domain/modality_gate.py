"""
Program × profile × primary_modality pairing for process packs.

Shared control-plane seams are allowed; science DAGs and actionConfig namespaces
must stay pack-owned (methylation vs rnaseq vs proteomics).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

from methyl_utils.analyte_profiles import normalize_primary_modality

# Actions that must not run outside the methylation process pack.
METHYL_ONLY_ACTIONS = frozenset(
    {
        "pipeline.centroid",
        "pipeline.detector",
        "pipeline.mapper",
        "pipeline.enricher",
        "pipeline.cell_deconvolution",
        "pipeline.residualize_fit",
        "pipeline.methylation_confounder_scores",
        "pipeline.derived_measures",
        "pipeline.info_measures",
        "pipeline.progression",
        "validation.plan_iterations",
        "validation.stability",
        "validation.stability_freeze_readiness",
        "validation.prepare_freeze_project",
        "validation.finalize_freeze_model_bundle",
        "validation.model_mc",
        "sample.parabricks_fq2bam",
        "sample.methyl_qc",
        "sample.methyl_extract",
        "sample.fragmentomics",
        "sample.extraction_qc",
    }
)


def _token(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    # Prefer basename stem for program/profile paths.
    name = Path(text).stem if ("/" in text or text.endswith(".json")) else text
    return name.lower().replace("-", "_")


def infer_program_family(program: Any) -> Optional[str]:
    """Map DomainProgram name/path → process-pack family."""
    t = _token(program)
    if not t:
        return None
    if "rnaseq" in t or t.startswith("rna_"):
        return "rnaseq"
    if "proteomics" in t or "protein" in t:
        return "proteomics"
    methyl_markers = (
        "samd_",
        "full_lifecycle",
        "study_validation",
        "sample_prep",
        "mc_",
        "validation_",
        "staged_",
        "buffy",
    )
    if any(m in t or t.startswith(m.rstrip("_")) for m in methyl_markers):
        # sample_prep_rnaseq / sample_prep_proteomics already handled above
        if "rnaseq" in t:
            return "rnaseq"
        if "proteomics" in t:
            return "proteomics"
        return "methylation"
    return None


def infer_profile_family(profile: Any) -> Optional[str]:
    """Map pipeline profile name/path → process-pack family."""
    t = _token(profile)
    if not t:
        return None
    if t.startswith("rnaseq") or "rnaseq" in t:
        return "rnaseq"
    if t.startswith("proteomics") or "proteomics" in t:
        return "proteomics"
    if (
        t.startswith("samd_")
        or t.startswith("staged_")
        or t.startswith("mc_")
        or "buffy" in t
        or t.startswith("cell_deconv")
        or t.startswith("gene_")
    ):
        return "methylation"
    return None


def context_modality(context: Mapping[str, Any]) -> Optional[str]:
    reg = context.get("regulatory") if isinstance(context.get("regulatory"), dict) else {}
    modality = normalize_primary_modality(reg.get("primary_modality"))
    if modality:
        return modality
    # Infer from program/profile when study omits primary_modality.
    program = (
        context.get("program_path")
        or context.get("program")
        or context.get("programPath")
    )
    profile = context.get("pipelineProfile") or context.get("profilePath")
    return infer_program_family(program) or infer_profile_family(profile)


def enforce_pack_pairing(context: Mapping[str, Any]) -> None:
    """
    Raise ValueError when program / profile / modality families disagree.

    Missing signals are skipped (partial contexts in unit tests). When two or more
    signals are present they must agree.
    """
    program = (
        context.get("program_path")
        or context.get("program")
        or context.get("programPath")
    )
    profile = context.get("pipelineProfile") or context.get("profilePath")
    reg = context.get("regulatory") if isinstance(context.get("regulatory"), dict) else {}
    modality = normalize_primary_modality(reg.get("primary_modality"))

    families: list[Tuple[str, str]] = []
    pf = infer_program_family(program)
    if pf:
        families.append(("program", pf))
    pr = infer_profile_family(profile)
    if pr:
        families.append(("profile", pr))
    if modality:
        families.append(("primary_modality", modality))

    if len(families) < 2:
        return
    expected = families[0][1]
    for label, fam in families[1:]:
        if fam != expected:
            raise ValueError(
                f"Process-pack mismatch: {families[0][0]} implies {expected!r} but "
                f"{label} implies {fam!r}. Use matching DomainProgram, pipelineProfile, "
                f"and regulatory.primary_modality for each pack "
                f"(methylation | rnaseq | proteomics)."
            )


def enforce_study_primary_analyte(context: Mapping[str, Any]) -> None:
    """
    Methylation studies must set study-owned regulatory.primary_analyte.

    Profiles must not pin analyte; missing study analyte is a hard error for methyl packs.
    Bare unit-test contexts with no modality/program/profile signal are skipped.
    """
    modality = context_modality(context)
    if modality != "methylation":
        return
    reg = context.get("regulatory") if isinstance(context.get("regulatory"), dict) else {}
    analyte = reg.get("primary_analyte")
    if analyte is None or str(analyte).strip() == "":
        raise ValueError(
            "Methylation studies require regulatory.primary_analyte on the study "
            "(cfdna | buffy_coat | tissue | plant_tissue). "
            "Procedure profiles must not supply a default analyte."
        )


def refuse_methyl_action_for_modality(action_name: str, modality: Optional[str]) -> None:
    """Raise if a methylation-only action is claimed under a non-methyl modality."""
    if action_name not in METHYL_ONLY_ACTIONS:
        return
    mod = normalize_primary_modality(modality) or "methylation"
    if mod != "methylation":
        raise ValueError(
            f"Action {action_name!r} is methylation-pack-only; "
            f"refusing under primary_modality={mod!r}."
        )
