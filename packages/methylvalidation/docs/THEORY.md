# MethylValidation Theoretical Foundation

The canonical mathematical and statistical reference for this package is the theory chapter [`docs/theory/chapters/05-methylpredictor-and-validation.md`](../../../docs/theory/chapters/05-methylpredictor-and-validation.md).

## Scope

`methylvalidation` estimates how pipeline metrics vary across repeated train/validation splits. It does not define a new probabilistic model for methylation or classification. Instead it:

- splits cohorts into train and validation sets,
- runs the full pipeline repeatedly,
- aggregates per-run metrics into empirical summaries.

## Method Status

- **Principled**: descriptive aggregation of metrics across repeated runs.
- **Operational**: split generation, project templating, subprocess orchestration.
- **Heuristic framing**: the resulting distributions are empirical Monte Carlo summaries, not exact sampling distributions for an external population.

## Key Point

This package supports internal repeated-split validation. It should not be described as a substitute for independent external validation.

## Backend Configuration Contract

Model backend behavior is controlled by `actionConfig.validation.backend_profiles` only. Legacy flat backend keys are intentionally rejected to avoid ambiguous runtime semantics.

- `--model-mc-all` evaluates only profiles with `enabled: true`.
- Single-backend modes require the selected backend to be enabled.
- Migration from legacy keys is explicit via `methyl-validation-migrate-backend-config`.

## Key Code Paths

- `methyl_validation/split.py`
- `methyl_validation/project_gen.py`
- `methyl_validation/pipeline_runner.py`
- `methyl_validation/validator_metrics.py`
