"""
Analyte-driven step_config defaults for MethylPipeline projects.

When ``step_config.validation.regulatory.primary_analyte`` is set and
``auto_apply_analyte_profile`` is not false, missing keys under each step are
filled from a profile (cfDNA vs buffy coat vs combined). Explicit project JSON
always wins (deep setdefault merge).
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

# Tokens treated as cfDNA after analyte_token() (lowercase, hyphen → underscore).
# Kept as aliases on context_json.primaryAnalyte; isCfdna is the boolean consumers use.
CFDNA_ANALYTE_TOKENS = frozenset(
    {"cfdna", "cf_dna", "cell_free_dna", "plasma", "plasma_cfdna"}
)


def analyte_token(value: Optional[str]) -> Optional[str]:
    """Trim, lowercase, and map hyphens to underscores. Does not collapse aliases."""
    if value is None:
        return None
    token = str(value).strip().lower().replace("-", "_")
    return token or None


def is_cfdna_analyte(value: Optional[str]) -> bool:
    token = analyte_token(value)
    return bool(token) and token in CFDNA_ANALYTE_TOKENS


def normalize_primary_modality(value: Optional[str]) -> Optional[str]:
    """Normalize regulatory primary_modality tokens to a canonical omics modality.

    Modality is the assay/omics axis (``methylation`` vs ``rnaseq``), distinct from
    ``primary_analyte`` which encodes the DNA methylation sample matrix
    (cfDNA vs buffy coat). Unknown values pass through cleaned so operators can
    introduce future modalities via config without a code change.
    """
    if value is None:
        return None
    cleaned = "_".join(str(value).strip().lower().replace("-", " ").split())
    if cleaned in {"methylation", "wgbs", "methyl", "dna_methylation", "bisulfite"}:
        return "methylation"
    if cleaned in {"rnaseq", "rna_seq", "rna", "transcriptomics", "transcriptome"}:
        return "rnaseq"
    if cleaned in {"proteomics", "proteome", "protein", "ms_proteomics", "mass_spec"}:
        return "proteomics"
    return cleaned or None


def normalize_primary_analyte(value: Optional[str]) -> Optional[str]:
    """Normalize regulatory primary_analyte tokens to canonical profile keys."""
    if value is None:
        return None
    cleaned = "_".join(str(value).strip().lower().replace("-", " ").split())
    if cleaned in {"cfdna", "cf_dna", "cell_free_dna", "plasma", "plasma_cfdna"}:
        return "cfdna"
    if cleaned in {"buffy_coat", "buffy", "buffy_coat_dna", "wbmc"}:
        return "buffy_coat"
    if cleaned in {"combined", "mixed"}:
        return "combined"
    if cleaned in {
        "plant_tissue",
        "plant",
        "leaf",
        "plant_leaf",
        "root_tissue",
        "root",
        "meristem",
        "seed",
    }:
        return "plant_tissue"
    return cleaned or None


def _deep_setdefault(target: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively fill missing keys in ``target`` from ``defaults``."""
    for key, val in defaults.items():
        if key not in target:
            target[key] = copy.deepcopy(val)
        elif isinstance(val, dict) and isinstance(target.get(key), dict):
            _deep_setdefault(target[key], val)
    return target


def profile_for_analyte(analyte: str) -> Dict[str, Dict[str, Any]]:
    """
    Return nested step_config patches for a canonical analyte.

    Keys are step names: alignment_qc, fragmentomics, enricher, validation.
    """
    key = normalize_primary_analyte(analyte) or analyte

    bisulfite_default = {
        "enabled": True,
        "source": "auto",
        "min_conversion_rate_pct": 99.0,
        "max_non_cpg_methylation_pct": 2.0,
    }

    if key == "cfdna":
        return {
            "alignment_qc": {
                "auto_profile_from_analyte": True,
                "fragmentomics": {"enabled": True, "profile": "cfdna"},
                "bisulfite_conversion": bisulfite_default,
                "alignment_guardrails": {
                    "enabled": True,
                    "min_mapping_rate": 0.98,
                    "max_secondary_supplementary_rate": 0.05,
                    "flagstat_enabled": True,
                    "min_properly_paired_rate": 0.90,
                    "max_supplementary_rate_flagstat": 0.02,
                },
            },
            "fragmentomics": {
                "enabled": True,
                "modes": ["wps", "end_motifs"],
                "end_motif_k": 4,
                "wps_bin_bp": 1000,
            },
            "enricher": {
                "library_preset": "cancer-core",
                "cisbp": {
                    "enabled": True,
                    "cisbp_modes": ["gene_sets", "motif_scan", "annotate"],
                    "species": "Homo_sapiens",
                },
            },
            "validation": {
                "enforce_training_analyte_match": True,
            },
        }

    if key == "buffy_coat":
        return {
            "alignment_qc": {
                "auto_profile_from_analyte": False,
                "bisulfite_conversion": bisulfite_default,
                "alignment_guardrails": {
                    "enabled": True,
                    "min_mapping_rate": 0.98,
                    "max_secondary_supplementary_rate": 0.05,
                    "flagstat_enabled": True,
                    "min_properly_paired_rate": 0.90,
                    "max_supplementary_rate_flagstat": 0.02,
                },
            },
            "fragmentomics": {
                "enabled": False,
            },
            "enricher": {
                "cisbp": {
                    "enabled": True,
                    "cisbp_modes": ["gene_sets"],
                    "species": "Homo_sapiens",
                },
            },
            "validation": {
                "enforce_training_analyte_match": False,
            },
        }

    if key == "plant_tissue":
        # Plant WGBS: CG/CHG/CHH are all real biology. Non-CpG methylation is a
        # signal, not a bisulfite-conversion failure, so the conversion-rate gate
        # is kept (spike-in / sidecar) while the non-CpG cap is opened up and the
        # mammalian CHG/CHH extraction caps are lifted. No blood fragmentomics.
        plant_bisulfite = {
            "enabled": True,
            "source": "auto",
            "min_conversion_rate_pct": 99.0,
            "max_non_cpg_methylation_pct": 100.0,
        }
        return {
            "alignment_qc": {
                "auto_profile_from_analyte": False,
                "bisulfite_conversion": plant_bisulfite,
                "alignment_guardrails": {
                    "enabled": True,
                    "min_mapping_rate": 0.90,
                    "max_secondary_supplementary_rate": 0.05,
                    "flagstat_enabled": True,
                    "min_properly_paired_rate": 0.90,
                    "max_supplementary_rate_flagstat": 0.02,
                },
            },
            "fragmentomics": {
                "enabled": False,
            },
            "extraction_qc": {
                "guardrails": {
                    "max_chh_methylation_level": 1.0,
                    "max_chg_methylation_level": 1.0,
                },
            },
            "enricher": {
                "library_preset": "plant-stress-core",
                "organism": "Arabidopsis_thaliana",
            },
            "validation": {
                "enforce_training_analyte_match": False,
            },
        }

    # combined / unknown: bisulfite only; no cfDNA fragmentomics auto-enable
    return {
        "alignment_qc": {
            "bisulfite_conversion": bisulfite_default,
        },
    }


def merge_step_config(
    step_name: str,
    user_cfg: Optional[Dict[str, Any]],
    analyte: Optional[str],
) -> Dict[str, Any]:
    """Merge profile defaults into a step config without overriding user keys."""
    out: Dict[str, Any] = copy.deepcopy(user_cfg) if user_cfg else {}
    if not analyte:
        return out
    profile = profile_for_analyte(analyte)
    patch = profile.get(step_name)
    if patch:
        _deep_setdefault(out, patch)
    return out


def should_apply_analyte_profile(regulatory: Optional[Dict[str, Any]]) -> bool:
    """
    True when profile merge should run.

    Default: apply when ``primary_analyte`` is set unless
    ``auto_apply_analyte_profile`` is explicitly false.
    """
    if not isinstance(regulatory, dict):
        return False
    primary = normalize_primary_analyte(regulatory.get("primary_analyte"))
    if not primary:
        return False
    flag = regulatory.get("auto_apply_analyte_profile")
    if flag is False:
        return False
    return True


def cisbp_mode_label(mode: str, base_label: str = "CIS-BP") -> str:
    """Merge library label for a CIS-BP mode (distinct files per mode)."""
    mode = (mode or "gene_sets").strip().lower()
    if mode == "gene_sets":
        return base_label
    if mode == "motif_scan":
        return f"{base_label}-motif"
    if mode == "annotate":
        return f"{base_label}-annotate"
    return f"{base_label}-{mode}"


def resolve_cisbp_modes(cfg: Any) -> List[str]:
    """Ordered CIS-BP modes: gene_sets, motif_scan, annotate (annotate last)."""
    modes_raw = getattr(cfg, "cisbp_modes", None)
    if modes_raw:
        order = {"gene_sets": 0, "motif_scan": 1, "annotate": 2}
        unique = []
        seen = set()
        for m in modes_raw:
            key = str(m).strip().lower()
            if key and key not in seen:
                seen.add(key)
                unique.append(key)
        return sorted(unique, key=lambda x: order.get(x, 99))

    single = (getattr(cfg, "mode", None) or "gene_sets").strip().lower()
    return [single]
