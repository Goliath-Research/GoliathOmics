# MethylMapper {#sec-methylmapper}
## Role

`methylmapper` converts locus-level detector output into gene- and region-level annotations. It has two distinct mathematical layers:

1. deterministic genomic mapping through bedtools-style interval logic or an external Azure SQL stored procedure, and
2. gene-level aggregation and disease-evidence enrichment.

The relevant code lives primarily in:

- `packages/methylmapper/methyl_mapper/bedtools_mapper.py`
- `packages/methylmapper/methyl_mapper/mapper.py`
- `packages/methylmapper/methyl_mapper/gene_disease_enricher.py`

## Interval Mapping

The bedtools path maps DMP coordinates to genomic features such as promoters, exons, introns, and gene bodies. This part is deterministic interval algebra rather than statistical inference. Feature-level weights may then be attached, for example promoter weights or exon weights, but those weights are biological priors chosen by the workflow rather than estimated model parameters.

The Azure SQL path calls a stored procedure (`spMapDMP2Genes`). Because the stored procedure logic is external to the repository, its exact mapping semantics should be documented as an external dependency rather than as a transparent in-repo algorithm.

## Gene-Level Combined p-Values

For genes with several mapped DMPs, the package computes a signed weighted Stouffer aggregation. Given DMP p-values $p_j$, weights $w_j$, and direction signs $s_j \in \{-1,+1\}$,

<div id="eq-gene-stouffer" markdown="1">

$$
z_j = \Phi^{-1}(1 - p_j/2),
\qquad
\tilde z_j = s_j z_j,
$$

$$
Z_{\text{gene}} =
\frac{\sum_j w_j \tilde z_j}
{\sqrt{\sum_j w_j^2}},
\qquad
p_{\text{gene}} = 2\left[1-\Phi(|Z_{\text{gene}}|)\right].
$$

</div>

The package then applies Storey q-values to the resulting gene-level p-values.

This is a standard meta-analytic construction in form [stouffer1949], but its interpretation requires care: mapped DMPs within one gene are correlated along the genome, so the independence assumption behind classical Stouffer aggregation is only approximate.

## Biological Importance Score

Gene- and feature-level biological importance aggregates detector `effect_size` with a configurable **biology-weight matrix** (feature × hyper/hypo). Per mapped DMP:

$$
w_i = f_i \cdot b(f_i, s_i), \qquad
|t_i| = w_i \cdot |e_i|, \qquad
\tilde t_i = \mathrm{sign}(\Delta\mu_i)\,|t_i|
$$

where $f_i$ is recurrence `frequency`, $e_i$ is detector `effect_size`, $b$ is the biology weight for feature bucket $f_i$ and direction $s_i$.

Gene-level rank:

$$
I_{\text{gene}} =
\left(\sum_i |t_i|\right)
\cdot
\frac{\left|\sum_i \tilde t_i\right|}{\sum_i |t_i|}
\cdot
\sqrt{\bar f}
$$

Feature-level columns `feature_importance_{promoter,exon,...}` use the same pattern within each bucket. Full defaults and export columns are documented in `packages/methylmapper/docs/BIOLOGICAL_IMPORTANCE_AUDIT.md`.

Downstream packages sort and weight genes by `gene_importance`, not row-averaged legacy summaries.

## Stable Gene Set Optimization

The package also contains routines that choose the number of top DMPs required to obtain a stable gene set. These routines are operationally useful, but they are not estimators with standard sampling distributions. Their parameters, step sizes, and stability thresholds should therefore be documented as tuning knobs.

## External Evidence Enrichment

`GeneDiseaseEnricher` queries external services such as DisGeNET [pinero2020], Open Targets [ochoa2023], and optional Grok-mediated evidence pipelines. This is a valuable biological annotation layer, but it is not in-repo statistical inference. The final evidence depends on:

- service availability,
- service version,
- cached responses,
- threshold profiles such as `strict` or `balanced`, and
- optional LLM-assisted synthesis.

For publication, that layer must be framed as external evidence gathering and summarization rather than as a mathematically identified part of the detector.

## Publication Guidance

The most defensible description is:

- interval mapping is deterministic annotation,
- gene-level p-values are weighted Stouffer aggregates with dependence caveats,
- gene importance is heuristic,
- and disease evidence is external-service-backed annotation.

That is still scientifically useful, but it is not the same kind of object as the upstream ECDF hypothesis tests.

## Mapper As Biological Validation Layer

Although `methylmapper` is downstream from DMP detection, it is a key validation checkpoint for selection quality. The practical question is whether selected loci project to coherent gene-level biology:

- Do top genes retain known lineage markers?
- Do mapped signals remain stable across runs and cohorts?
- Do gene-level aggregates bridge naturally into enrichment/module themes?

In this sense, mapper outputs are not only annotations; they are a sanity check that locus-level selection carries biologically meaningful structure into gene space.
