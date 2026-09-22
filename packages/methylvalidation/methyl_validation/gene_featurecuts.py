"""Backward-compatible re-export of gene FeatureCuts from methyl-gene-select."""

from methyl_gene_select.core import gene_featurecuts as _gene_featurecuts

for _name in dir(_gene_featurecuts):
    if _name.startswith("__"):
        continue
    globals()[_name] = getattr(_gene_featurecuts, _name)

del _gene_featurecuts, _name
