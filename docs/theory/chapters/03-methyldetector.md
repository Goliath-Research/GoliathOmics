# MethylDetector {#sec-methyldetector}
## Role

`methyldetector` turns aligned centroids into a filtered set of differential methylation positions (DMPs) and exports the classifier artifacts used downstream. The main implementation is `packages/methyldetector/methyl_detector/core/methyldetector.py`.

Conceptually, the detector is a two-stage pipeline:

1. statistical screening of loci, and
2. biological prioritization and export.

That split is important. The code does not rely on a single monolithic significance score.

## Alignment And Candidate Set

The detector starts from aligned centroid pairs, typically one control centroid and one disease centroid for a given chromosome and context. The alignment step reduces both centroids to the common set of genomic positions and propagates the empirical summaries described in [§ methylutils](01-methylutils.md#sec-methylutils) and [§ methylcentroid](02-methylcentroid.md#sec-methylcentroid).

Let $\mathcal{I}$ denote the set of aligned loci under consideration for one comparison.

## Statistical Screening

The detector supports two closely related statistical paths:

- a histogram-based path through `MethylCentroidPair.compare_centroids()`, and
- a more precise ECDF path that recomputes KS statistics directly from reconstructed empirical distributions.

In both cases the active workflow is:

1. compute one p-value $p_i$ per locus $i \in \mathcal{I}$,
2. adjust all p-values to q-values using Storey q-values,
3. keep loci satisfying the configured q-value threshold and any minimum effect thresholds.

At the theory level this is

<div id="eq-detector-statset" markdown="1">

$$
\mathcal{D}_{\text{stat}} =
\left\{ i \in \mathcal{I} : q_i \le \alpha,\ |\Delta\mu_i| \ge \delta_{\min} \right\},
$$

</div>

with the exact choice of $\alpha$ and $\delta_{\min}$ coming from configuration rather than mathematical derivation.

## Biological Ranking

Once a statistically filtered set exists, the detector ranks loci using the canonical effect score introduced in [Eq. effect-size](01-methylutils.md#eq-effect-size). This gives a second set of priorities that is explicitly biological or practical rather than inferential.

The central design choice is that a locus can be:

- statistically strong but biologically modest,
- biologically large but not significant after multiplicity correction, or
- both.

The detector keeps these notions separate instead of compressing them into one scalar.

## Cumulative Effect Coverage

One of the package's characteristic filters is cumulative effect-mass selection. After sorting retained loci by decreasing effect size, the detector may keep only the smallest prefix whose cumulative effect mass reaches a specified fraction $\gamma$ of the total:

<div id="eq-effect-coverage" markdown="1">

$$
\sum_{i \in S_\gamma} e_i
\ge
\gamma \sum_{i \in \mathcal{D}_{\text{stat}}} e_i,
$$

</div>

where $e_i$ is the per-locus effect score.

This is a useful way to compress large DMP sets, but it is a heuristic summarization rule rather than a statistical decision boundary with a formal optimality guarantee.

## Context And Chromosome Granularity

The detector is designed to run at chromosome and context granularity. In practical terms this means:

- each classifier artifact is tied to a specific chromosome,
- contexts such as `CG`, `CHG`, and `CHH` can be modeled separately or together, and
- any final multiclass or multi-chromosome system is assembled later by `methylclassifier`.

That architecture keeps detection local and composable, but it also means that downstream fusion is not part of the detector's statistical test itself.

## Exported Classifier Artifacts

The detector does more than report DMPs. It also packages the information needed by the ECDF classifier:

- the filtered DMP positions,
- per-class centroid histograms over those positions,
- directions of methylation change, and
- feature weights such as effect size.

Mathematically, this means the detector defines the feature space and the feature weighting scheme for the classifier. The classifier is therefore downstream of detector choices even when its own scoring rule is fixed.

## Discovery versus prediction exports

Interpretation workflows (mapping DMPs to genes, pathway enrichment, comparing stages) benefit from **broader, biologically coherent** DMP lists. Predictive modeling benefits from **smaller, stable** feature sets chosen without conflating biological narrative with classification utility.

The implementation therefore supports two coordinated outputs when `dmp_export_mode=dual` (default in project configs):

- `dmps-{chrom}-discovery.csv`: all biologically filtered loci after importance ranking, **without** the effect-size elbow used to trim the classifier panel (unless `discovery_dynamic_dmp_cutoff_enabled` is turned on).
- `dmps-{chrom}-selected.csv`: the **primary prediction panel** after FeatureCuts balanced-accuracy optimization or the effect-size elbow (`classifier_dmp_selection=featurecuts_validation` when validation samples are configured). Legacy `dmps-{chrom}-classifier.csv` remains for one release cycle.

The saved ECDF classifier pickle always uses the **selected** panel, not the discovery table. MethylMapper and MethylEnricher should read the **discovery** glob (for example `dmps-*-discovery.csv`) in discovery-only modeling modes; FeatureCuts workflows prefer `dmps-*-selected.csv`. Multiclass merge utilities resolve `dmps-*-selected.csv` automatically when present.

For strict evaluation of a deployed classifier, feature selection must be performed **inside** each outer training fold (for example by running detection on fold-specific centroids, as in Monte Carlo train splits); discovery lists built on all samples may still be reported for mechanism, but they must not be the sole basis for performance claims unless that design is stated explicitly.

## Production Freeze Panel {#sec-detector-fixed-panel}
In the production workflow ([§ two workflows](12-two-workflows.md#sec-two-workflows)), the detector can bypass statistical and biological discovery entirely and instead operate from a fixed list of pre-selected loci. This is configured via the `fixed_dmp_panel` parameter in profile `actionConfig.detection`.

When `fixed_dmp_panel` is set to a path pointing at a CSV file that contains at minimum the columns `chromosome` and `position`, the `_run_multi_context` method short-circuits the entire discovery pipeline. No centroids are compared, no KS statistics are computed, and no biological filtering is applied. The columns `context`, `effect_size`, `statistical_dmp`, and `biological_dmp` are inferred or set to unit defaults for any that are missing from the file. The result object is constructed directly from the loaded rows, with `biological_retention_rate = 1.0` and `n_statistical_dmps = n_biological_dmps = n_classifier_dmps` all equal to the CSV row count.

This mechanism serves the production workflow where:

1. DMP positions were already validated across many Monte Carlo splits (stability analysis),
2. only the most reliably recurring positions are retained in the stable panel, and
3. the final model must use exactly those positions on the full dataset without re-running discovery.

The code path is:

```
MethylDetector._run_multi_context()
  ↓ if config.fixed_dmp_panel is not None
  ↓ pd.read_csv(fixed_path)
  ↓ validate columns {"chromosome", "position"} ⊆ columns
  ↓ infer missing context / effect_size / flags
  ↓ export CSV + save classifier model
  ↓ return MethylDetectorResult (no discovery performed)
```

The exported classifier model (PKL) is identical in structure to one built through normal discovery; downstream tools (`methylclassifier`, `methylpredictor`) are unaware of whether the positions came from live detection or a fixed panel.

**Theoretical note.** Using a fixed panel from a previous analysis is a form of transfer. The panel was selected on a different (possibly overlapping) set of samples. The validity of downstream statistical claims depends on whether the panel samples and the inference samples are from the same population. This is an operational design choice, not a statistical guarantee; it should be stated explicitly in any publication that uses the freeze workflow.

## DMP Coverage in New Samples {#sec-detector-coverage}
A frequently encountered diagnostic problem is low DMP coverage in validation or prediction samples: the classifier reports that only 10–30% of DMP positions have usable observations in a given sample. The root cause is almost always a mismatch between coverage thresholds **at centroid construction / DMP discovery** and **at MethylClassifier inference**:

- `min_coverage` in profile `actionConfig.centroid.base_config` and matching `min_coverage` in `actionConfig.detection` — minimum reads per locus when building centroids and when calling DMPs.
- **`min_coverage` (or equivalent) in profile `actionConfig.classifier` / MethylClassifier JSON** — minimum reads required to treat a locus as observed in a *new* sample.

If centroids and detection used `min_coverage = 4` but the classifier requires a higher per-locus depth, loci with 4–9 reads in a test sample are dropped from the feature matrix even though they were part of the DMP panel.

The observable symptom is:

```
sample_dmp_coverage.dmps_used_fraction_median < 0.5
```

in `validation_metrics.json`. Values below 0.3 indicate severe coverage mismatch and nearly always lead to prediction dominated by the majority class.

**Recommended practice:** Set **MethylClassifier** `min_coverage` (see classifier docs / schema) to match the centroid/detector `min_coverage` used during training (commonly `4`). Raise it only when test samples are known to be much deeper than training and locus-level precision is paramount.

## Publication Guidance

The most accurate publication description is:

- `methyldetector` performs nonparametric locus-wise screening,
- applies FDR control through Storey q-values,
- supplements significance with heuristic biological ranking,
- and exports a reduced, weighted empirical-distribution feature set.

When the `fixed_dmp_panel` path is used, the package should be described as applying a pre-specified position set rather than performing novel discovery.

The package should **not** be described as a pure hypothesis-testing engine if the effect-coverage filters are active, because those filters are explicitly post-test prioritization rules.
