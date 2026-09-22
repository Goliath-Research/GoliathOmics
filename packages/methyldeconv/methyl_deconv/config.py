"""Pydantic step config for pipeline.cell_deconvolution (resolved via actionConfig)."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class CellDeconvStepConfig(BaseModel):
    """Schema-facing actionConfig (tunable knobs default to None until site/profile set them)."""

    model_config = ConfigDict(extra="ignore")

    output_dir: Optional[str] = Field(
        default=None,
        description="Output directory; default {project_root}/cell_fractions.",
    )
    method: Optional[Literal["houseman", "hitimed"]] = Field(
        default=None,
        description=(
            "Deconvolution method. 'houseman' (default) runs one flat FlowSorted/IDOL "
            "projection; 'hitimed' runs the analyte-driven hierarchical tree. "
            "Operator-set per profile/site."
        ),
    )
    seed_basis_path: Optional[str] = Field(
        default=None,
        description=(
            "Path to FlowSorted/IDOL seed basis JSON (markers × cell-type betas) for the "
            "'houseman' method. Operator-set per site/profile; defaults to packaged "
            "flowsorted_blood_epic_idol_v1.json."
        ),
    )
    hierarchy_basis_path: Optional[str] = Field(
        default=None,
        description=(
            "Path to hierarchical (v2) basis JSON for the 'hitimed' method. Operator-set "
            "per site/profile; defaults to the packaged hierarchy basis. The tree used is "
            "selected by analyte via the basis' analyte_trees map."
        ),
    )
    analyte: Optional[str] = Field(
        default=None,
        description=(
            "Analyte selecting the hierarchical tree (buffy_coat | cfdna | tissue) for the "
            "'hitimed' method. Defaults to the project regulatory primary_analyte."
        ),
    )
    contexts: Optional[List[Literal["CG", "CHG", "CHH"]]] = Field(
        default=None,
        description=(
            "Methylation contexts to read from sample H5. "
            "Required in site/profile actionConfig.cell_deconvolution (no code default)."
        ),
    )
    marker_min_coverage: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Minimum coverage at a marker CpG for inclusion in Y. "
            "Required in site/profile actionConfig.cell_deconvolution (no code default)."
        ),
    )
    min_marker_fraction: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum fraction of seed markers observed (with coverage) required to emit Ω; "
            "below this, proportions are NaN. "
            "Required in site/profile actionConfig.cell_deconvolution (no code default)."
        ),
    )
    use_gpu: Optional[bool] = Field(
        default=None,
        description=(
            "Prefer MethylUtils CuPy/GPU backend when available (honors METHYL_DISABLE_GPU). "
            "Operator-set per profile/site; same pattern as centroid use_gpu."
        ),
    )
    sample_id_column: str = Field(
        default="sample_id",
        description="Column name for sample identifier in cell_fractions.csv.",
    )

    def require_runtime(self) -> "CellDeconvRuntimeParams":
        """Validate operator-required knobs and return a typed runtime model (no code defaults)."""
        return CellDeconvRuntimeParams.from_step_config(self)


class CellDeconvRuntimeParams(BaseModel):
    """Resolved deconvolution knobs after site/profile merge; science thresholds are required."""

    model_config = ConfigDict(extra="forbid")

    method: Literal["houseman", "hitimed"] = Field(
        default="houseman",
        description="Resolved deconvolution method (default houseman).",
    )
    contexts: List[Literal["CG", "CHG", "CHH"]] = Field(
        min_length=1,
        description="Methylation contexts to read from sample H5.",
    )
    marker_min_coverage: int = Field(
        ge=1,
        description="Minimum coverage at a marker CpG for inclusion in Y.",
    )
    min_marker_fraction: float = Field(
        ge=0.0,
        le=1.0,
        description="Minimum fraction of seed markers observed required to emit Ω.",
    )
    use_gpu: Optional[bool] = Field(
        default=None,
        description="Prefer MethylUtils CuPy/GPU when available.",
    )
    analyte: Optional[str] = Field(
        default=None,
        description="Analyte selecting the hierarchical tree for the hitimed method.",
    )
    sample_id_column: str = Field(
        default="sample_id",
        description="Column name for sample identifier in cell_fractions.csv.",
    )

    @classmethod
    def from_step_config(cls, cfg: CellDeconvStepConfig) -> "CellDeconvRuntimeParams":
        missing = [
            name
            for name, value in (
                ("contexts", cfg.contexts),
                ("marker_min_coverage", cfg.marker_min_coverage),
                ("min_marker_fraction", cfg.min_marker_fraction),
            )
            if value is None
        ]
        if missing:
            raise ValueError(
                "cell_deconvolution requires operator-set config (site/profile actionConfig): "
                f"missing {', '.join(missing)}"
            )
        return cls(
            method=str(cfg.method or "houseman"),  # type: ignore[arg-type]
            contexts=list(cfg.contexts),
            marker_min_coverage=int(cfg.marker_min_coverage),
            min_marker_fraction=float(cfg.min_marker_fraction),
            use_gpu=cfg.use_gpu,
            analyte=cfg.analyte,
            sample_id_column=str(cfg.sample_id_column or "sample_id"),
        )
