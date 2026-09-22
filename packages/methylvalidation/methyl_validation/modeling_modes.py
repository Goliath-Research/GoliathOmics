"""
Process-agnostic DMP / gene modeling mode resolution for Monte Carlo workflows.

Maps statistical alternatives (raw pool, FeatureCuts, stable panel, mapper-ranked genes)
to pipeline IF flags, stability axes, and artifact source selectors.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Mapping, Optional

DmpModelingMode = Literal["raw_pool", "featurecuts", "stable_panel"]
GeneModelingMode = Literal["none", "mapper_ranked", "featurecuts", "from_stable_dmp_panel"]
GeneRecurrenceSource = Literal["enricher", "mapper", "classifier"]
GeneFeaturecutsLociSource = Literal["raw_pool", "featurecuts_selected", "stable_panel"]

def normalize_dmp_modeling_mode(value: Optional[str]) -> Optional[DmpModelingMode]:
    if value is None:
        return None
    key = str(value).strip().lower()
    if key in ("raw_pool", "featurecuts", "stable_panel"):
        return key  # type: ignore[return-value]
    return None


def normalize_gene_modeling_mode(value: Optional[str]) -> Optional[GeneModelingMode]:
    if value is None:
        return None
    key = str(value).strip().lower()
    if key in ("none", "mapper_ranked", "featurecuts", "from_stable_dmp_panel"):
        return key  # type: ignore[return-value]
    return None


def normalize_gene_featurecuts_loci_source(value: Optional[str]) -> GeneFeaturecutsLociSource:
    if value is None:
        return "raw_pool"
    key = str(value).strip().lower()
    # Map historical detector tokens still stored on stability_gene_featurecuts_dmp_source.
    if key == "discovery":
        key = "raw_pool"
    elif key == "classifier":
        key = "featurecuts_selected"
    elif key == "stable":
        key = "stable_panel"
    if key in ("raw_pool", "featurecuts_selected", "stable_panel"):
        return key  # type: ignore[return-value]
    return "raw_pool"


def infer_dmp_modeling_mode(config: Mapping[str, Any]) -> DmpModelingMode:
    explicit = normalize_dmp_modeling_mode(config.get("dmp_modeling_mode"))
    if explicit is not None:
        return explicit
    if bool(config.get("stability_featurecuts_enabled")) or bool(config.get("runDmpSelection")):
        return "featurecuts"
    return "raw_pool"


def infer_gene_modeling_mode(config: Mapping[str, Any]) -> GeneModelingMode:
    explicit = normalize_gene_modeling_mode(config.get("gene_modeling_mode"))
    if explicit is not None:
        return explicit
    if bool(config.get("stability_gene_featurecuts_enabled")) or bool(config.get("runGeneFeaturecuts")):
        return "featurecuts"
    loci = config.get("gene_featurecuts_loci_source") or config.get("stability_gene_featurecuts_dmp_source")
    if normalize_gene_featurecuts_loci_source(str(loci) if loci else None) == "stable_panel":
        return "from_stable_dmp_panel"
    return "none"


def infer_gene_recurrence_source(config: Mapping[str, Any]) -> GeneRecurrenceSource:
    """Resolve gene-panel provenance for stability aggregation.

    Modeling mode wins over a stale explicit ``enricher`` value: gene FeatureCuts
    panels come from methyl-mapper + FeatureCuts (``classifier``), and mapper-ranked
    mode uses mapper tables. Enricher hubs are optional and typically applied at
    freeze, not as the MC gene-recurrence source for gene FeatureCuts packs.
    """
    gene_mode = infer_gene_modeling_mode(config)
    if gene_mode == "mapper_ranked":
        return "mapper"
    if gene_mode == "featurecuts" or gene_mode == "from_stable_dmp_panel":
        return "classifier"
    if bool(config.get("prefer_classifier_gene_panels")) or bool(config.get("stability_gene_featurecuts_enabled")):
        return "classifier"
    if bool(config.get("prefer_mapper_gene_panels")):
        return "mapper"
    explicit = config.get("stability_gene_recurrence_source")
    if explicit in ("enricher", "mapper", "classifier"):
        return explicit  # type: ignore[return-value]
    return "enricher"


def apply_modeling_modes_to_validation_dict(validation: Dict[str, Any]) -> Dict[str, Any]:
    """Derive legacy boolean flags and BA aliases from explicit modeling mode axes."""
    out = dict(validation)
    dmp_mode = infer_dmp_modeling_mode(out)
    gene_mode = infer_gene_modeling_mode(out)

    out["dmp_modeling_mode"] = dmp_mode
    out["gene_modeling_mode"] = gene_mode

    out["stability_featurecuts_enabled"] = dmp_mode == "featurecuts"
    out["runDmpSelection"] = dmp_mode == "featurecuts"

    out["stability_gene_featurecuts_enabled"] = gene_mode in ("featurecuts", "from_stable_dmp_panel")
    out["runGeneFeaturecuts"] = gene_mode in ("featurecuts", "from_stable_dmp_panel")

    if gene_mode == "from_stable_dmp_panel":
        out["gene_featurecuts_loci_source"] = "stable_panel"
        out["stability_gene_featurecuts_dmp_source"] = "stable"
        out["stability_gene_recurrence_source"] = "classifier"
    elif gene_mode == "featurecuts":
        loci = normalize_gene_featurecuts_loci_source(
            str(out.get("gene_featurecuts_loci_source") or out.get("stability_gene_featurecuts_dmp_source") or "")
        )
        out["gene_featurecuts_loci_source"] = loci
        # stability_gene_featurecuts_dmp_source retains historical detector tokens.
        out["stability_gene_featurecuts_dmp_source"] = (
            "discovery"
            if loci == "raw_pool"
            else "classifier"
            if loci == "featurecuts_selected"
            else "stable"
        )
        # Gene FC panels are exported under gene_stability/genes-classifier.csv;
        # do not leave profile defaults pointing at enricher hubs.
        out["stability_gene_recurrence_source"] = "classifier"
    elif gene_mode == "mapper_ranked":
        out["stability_gene_recurrence_source"] = "mapper"

    # Split BA targets (backward compatible with stability_target_balanced_accuracy).
    shared_ba = out.get("stability_target_balanced_accuracy")
    if out.get("dmp_featurecuts_target_ba") is None and shared_ba is not None:
        out["dmp_featurecuts_target_ba"] = shared_ba
    if out.get("gene_featurecuts_target_ba") is None and shared_ba is not None:
        out["gene_featurecuts_target_ba"] = shared_ba

    dmp_ba = out.get("dmp_featurecuts_target_ba")
    if dmp_ba is not None and dmp_mode == "featurecuts":
        out["stability_target_balanced_accuracy"] = dmp_ba

    gene_ba = out.get("gene_featurecuts_target_ba")
    if gene_ba is not None and out.get("stability_gene_featurecuts_enabled"):
        pass  # gene FC reads gene_featurecuts_target_ba via project_config alias

    min_dmps = out.get("dmp_featurecuts_min_dmps")
    if min_dmps is not None:
        out["stability_min_core_dmps"] = min_dmps

    max_dmps = out.get("dmp_featurecuts_max_dmps")
    if max_dmps is not None:
        out["stability_classifier_export_max_dmps"] = max_dmps

    min_genes = out.get("gene_featurecuts_min_genes")
    if min_genes is not None:
        out["stability_min_selected_genes"] = min_genes

    max_genes = out.get("gene_featurecuts_max_genes")
    if max_genes is not None:
        out["stability_gene_featurecuts_max_genes"] = max_genes

    return out


def validate_stability_min_balanced_accuracy_requires_featurecuts(
    validation: Mapping[str, Any],
) -> None:
    """
    Reject stability_min_balanced_accuracy when no FeatureCuts axis is enabled.

    Without DMP or gene FeatureCuts, per-run balanced_accuracy used for stability
    run filtering is unavailable (null/zero), so the threshold is meaningless.
    """
    derived = apply_modeling_modes_to_validation_dict(dict(validation))
    min_ba = derived.get("stability_min_balanced_accuracy")
    if min_ba is None:
        return
    if not bool(derived.get("stability_featurecuts_enabled")) and not bool(
        derived.get("stability_gene_featurecuts_enabled")
    ):
        raise ValueError(
            "stability_min_balanced_accuracy requires DMP or gene FeatureCuts: "
            "set dmp_modeling_mode=featurecuts and/or "
            "gene_modeling_mode=featurecuts|from_stable_dmp_panel. "
            "Without FeatureCuts, per-run balanced_accuracy is unavailable for stability filtering."
        )


def mapper_csv_pattern_for_dmp_mode(dmp_mode: DmpModelingMode) -> str:
    if dmp_mode == "stable_panel":
        return "stable_dmps*.csv"
    if dmp_mode == "featurecuts":
        return "dmps-*-selected.csv"
    return "dmps-*-discovery.csv"


def stability_dmp_axis(dmp_mode: DmpModelingMode) -> str:
    if dmp_mode == "featurecuts":
        return "classifier"
    if dmp_mode == "stable_panel":
        return "stable"
    return "discovery"


def stability_gene_axis(recurrence: GeneRecurrenceSource) -> str:
    return recurrence


def resolve_gene_stability_preferences(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Map MonteCarloConfig / validation dict to stability gene-axis kwargs."""
    source = infer_gene_recurrence_source(config)
    return {
        "prefer_classifier_gene_panels": source == "classifier",
        "prefer_mapper_gene_panels": source == "mapper",
        "gene_recurrence_source": source,
    }
