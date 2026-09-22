# MethylPredictor Theoretical Foundation

The canonical mathematical and statistical reference for this package is the theory chapter [`docs/theory/chapters/05-methylpredictor-and-validation.md`](../../../docs/theory/chapters/05-methylpredictor-and-validation.md).

## Scope

`methylpredictor` is the application and evaluation layer for trained classifiers. It does not fit a new methylation model. Instead it:

- runs a classifier on labeled or blind sample sets,
- computes standard classification metrics when labels are present,
- summarizes predictive uncertainty when labels are absent.

## Method Status

- **Principled**: accuracy, balanced accuracy, confusion matrix, precision/recall/F1, entropy summaries.
- **Operational**: project-driven path resolution and batch prediction workflows.

## Key Point

Blind-mode entropy is an uncertainty summary, not a performance estimate. Any discrimination claim requires labeled evaluation data.

## Key Code Paths

- `methyl_predictor/core/predictor.py`
- `methyl_predictor/project_resolver.py`
- `methyl_classifier/cli/main.py`
