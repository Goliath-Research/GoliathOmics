"""Backward-compatible re-export of biomarker gene pool."""

from methyl_gene_select.core import biomarker_gene_pool as _mod

for _name in dir(_mod):
    if _name.startswith("__"):
        continue
    globals()[_name] = getattr(_mod, _name)

del _mod, _name
