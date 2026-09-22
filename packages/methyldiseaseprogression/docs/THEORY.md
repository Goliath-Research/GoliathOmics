# MethylDiseaseProgression Theory

## Scope

`methyl-disease-progression` is a deterministic synthesis layer over upstream mapper/enricher outputs.
It does not discover loci directly; it converts stage-indexed ranked tables into progression summaries.

## Stage-Ordered Representation

For ordered stages `s = 0..K-1`, each entity `e` (gene/pathway/module) is represented by scores:

- `score(e, s)` when present for stage `s`
- missing otherwise

The package emphasizes reproducible tabulation over complex latent modeling.

## Pathway Score Convention

When pathway q-values are available, progression uses:

- `score = -log10(q)`

This provides monotonic interpretability (larger score = stronger significance) and avoids direct
mixing of p-value and odds-ratio scales in trend summaries.

## Progression Labels

Entity labels summarize stage coverage and simple directional patterns (for example stable across stages,
early-only, late-only, monotonic up/down variants). These labels are rule-based descriptors, not formal
causal claims.

## Interpretation Caveats

- Progression labels depend on upstream detector/mapper/enricher outputs and ranking conventions.
- Missing stages or sparse module tables can strongly affect directional summaries.
- Strong absolute correlations do not guarantee strict monotonic behavior across all adjacent stages.

Use progression summaries as structured evidence for review, not as stand-alone biological proof.
