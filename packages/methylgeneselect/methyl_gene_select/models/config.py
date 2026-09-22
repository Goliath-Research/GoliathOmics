"""Gene selection configuration (step_config.gene_selection)."""

from __future__ import annotations

from typing import List, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field


class GeneSelectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_balanced_accuracy: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    min_selected_genes: Optional[int] = Field(default=None, ge=1)
    max_genes: Optional[int] = Field(
        default=None,
        ge=1,
        description="Cap ranked gene pool; set in site/profile actionConfig.gene_selection.",
    )
    max_dmps: Optional[int] = Field(
        default=None,
        ge=1,
        description="Cap DMP loci for gene FC; set in site/profile actionConfig.gene_selection.",
    )
    biomarker_filter_enabled: bool = Field(default=False)
    biomarker_mode: Literal["ppi_only", "full_enricher"] = Field(default="ppi_only")
    biomarker_region_hits: Optional[List[str]] = Field(default=None)
    biomarker_min_degree: Optional[int] = Field(default=None, ge=0)
