"""Backward-compatible re-export of raw gene features."""

from methyl_gene_select.core import raw_gene_features as _mod

for _name in dir(_mod):
    if _name.startswith("__"):
        continue
    globals()[_name] = getattr(_mod, _name)

del _mod, _name
