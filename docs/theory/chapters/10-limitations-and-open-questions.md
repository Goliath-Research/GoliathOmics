# Limitations and Open Questions {#sec-limitations}
## Why This Chapter Exists

The repository is scientifically useful, but it is not served well by a perfectly tidy story. The code mixes exact formulas, approximations, heuristics, and external-service calls. This chapter records the main caveats that should be understood before publishing claims based on the pipeline.

## The “ECDF-Only Pipeline” Claim Is Too Broad

The centroid-detector-classifier path is indeed ECDF-centered. However:

- `methylmapper` uses weighted Stouffer aggregation and heuristic importance scores,
- `methylenricher` uses external enrichment plus heuristic module scoring,
- `methylalignmentqc` is deterministic QC parsing, not ECDF inference.

Therefore the monorepo as a whole should not be marketed as purely ECDF-only. The safer statement is that the **core supervised detection and classification path** is ECDF-centered.

## Dependence Is Real

Several methods rely on assumptions that are standard but imperfect for methylation data:

- loci within one chromosome are correlated,
- mapped DMPs within one gene are correlated,
- binary OvR heads are not guaranteed to be jointly compatible,
- repeated validation splits are not independent external studies.

This does not invalidate the workflow, but it does change how literally one should interpret p-values, q-values, and meta-analytic combinations.

## Approximation Layers

The code intentionally trades exact raw-data procedures for scalable summaries:

- KS statistics are evaluated on a grid and calibrated asymptotically,
- Mann-Whitney is reconstructed from histograms rather than raw ranks,
- ECDF overlap is integrated numerically after spline reconstruction,
- some distance calculations average only over pairwise-overlapping loci.

These approximations should be reported as approximations, not hidden behind textbook method names alone.

## Heuristic Layers

The following components are best described as engineering decisions:

- the canonical effect-size ranking,
- cumulative effect-mass selection,
- geometric-mean control aggregation for pairwise OvR bundles,
- module scoring constants in `methylenricher`,
- stability thresholds for choosing top DMP counts in `methylmapper`,
- auto-cap rules for coverage trimming.

These are often useful, but their outputs are not calibrated probabilities or test statistics.

## External Dependencies

Some results depend on systems whose internal logic is not fully versioned inside this repository:

- Enrichr,
- DisGeNET,
- Open Targets,
- Azure SQL stored procedures,
- optional Grok/xAI-assisted evidence synthesis.

Any publication using those layers should record service versions, access dates, and cache policies. Otherwise the results are reproducible only in a weak operational sense.

## Deconvolution Reference Bases

Cell-type deconvolution ([§ methyldeconv](07a-methyldeconv.md#sec-methyldeconv)) is a principled constrained projection, but its accuracy is bounded by the reference basis. Two provisioning caveats apply:

- Only the measured blood immune subtree (derived from the FlowSorted/IDOL basis) ships in the wheel. The cfDNA plasma top split and the full tissue tumor/immune/stromal tree are **composed offline from operator-supplied atlases** and are not fabricated at worker runtime; a study using the cfDNA `tumor_fraction` or tissue tumor/stromal leaves depends on those provisioned atlases.
- Deconvolution is deterministic and MC-free, so it carries no stability/recurrence estimate of its own. HiTIMED leaf proportions inherit whatever bias and noise the node bases and marker coverage impose; low marker coverage degrades gracefully to `partial` or `insufficient_markers` rather than a calibrated uncertainty.

## Legacy Or Experimental Remnants

The repository still contains legacy components, but their status is now clearer:

- The removed `packages/methylutils/comparison.py` beta-comparator path is **not** used by the active detector/classifier production flow (see §2.10 in Chapter 1).
- A few package-local docs can lag behind code evolution between releases.

To avoid methodological confusion, interpret these as **historical/auxiliary artifacts**, not active runtime estimators.

### Current safeguards

The current docs/code governance already reduces this risk:

1. The theory chapters (`docs/theory`) are the canonical method reference.
2. Production workflow chapters describe the active ECDF-first detector/classifier path and the current model backends explicitly.
3. Legacy modules remain in-repo for compatibility and experimentation, but publication claims should cite only methods documented as active in this book.

### Deprecation Status Snapshot

| Component / path | Status | Practical guidance |
|---|---|---|
| `methylutils` ECDF core (`core/distribution_views.py`, `ecdf_classifier.py`, centroid pair ECDF comparison path) | active | Safe to cite as current detector/classifier mathematical backbone. |
| `methylvalidation` model backends (`ecdf`, `tabular_sklearn`, `generative_hybrid`) | active | Safe to cite when backend and key hyperparameters are reported. |
| `methylmapper`, `methylenricher`, `methyldiseaseprogression` workflow synthesis | active | Safe to cite as downstream interpretation/synthesis layers (with heuristic/external-service caveats). |
| `methyldeconv` Houseman flat and HiTIMED hierarchical deconvolution | active | Safe to cite as constrained-projection composition; report the reference basis provenance (blood in-wheel vs operator-provisioned cfDNA/tissue atlases). |
| `methyl_utils/comparison.py` beta-comparator module | removed | Historical; do not describe as active production detector path. |
| Incomplete/retired experimental branches (historical clustering and partial DP-like ideas) | experimental | Do not cite as validated production methodology. |

## Publication-Ready Positioning

The strongest defensible manuscript framing is:

1. the core pipeline summarizes methylation into centroids with empirical distributions,
2. it detects DMPs with nonparametric, ECDF-based screening and FDR adjustment,
3. it classifies samples with weighted empirical-distribution likelihoods,
4. and it layers biological annotation and pathway-level interpretation on top.

Anything stronger than that risks overstating what the code actually does.

## What Would Strengthen The Theory Further

The current implementation could support stronger future papers if the repository adds:

- finite-sample benchmarking of histogram-based Mann-Whitney against raw-rank Mann-Whitney,
- calibration studies for the effective-temperature ECDF classifier,
- dependence-aware gene aggregation,
- more explicit modeling of multiclass consistency,
- and clearer retirement or isolation of legacy experimental branches.

Those are natural next steps, but they are not yet the state of the codebase.
