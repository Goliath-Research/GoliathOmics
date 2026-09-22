# Scope, Traceability, and Notation

## Purpose

This book documents the mathematical and statistical behavior that MethylPipeline actually implements. It is not a historical reconstruction of the first design notes, and it is not a promise about future refactors. The source of truth is the current code under `packages/`.

That rule has two consequences:

1. If the code and an older markdown document disagree, the code is canonical.
2. If the code uses an approximation or heuristic, the book says so explicitly instead of rewriting it into a cleaner but false theory story.

## What Counts As “Theory” In This Repository

The repository mixes several kinds of methodology. For publication-quality documentation it is important to separate them.

| Category | Meaning in this book | Typical examples in MethylPipeline |
|---|---|---|
| Principled | Standard statistical or numerical methods used in a recognizable form | asymptotic Kolmogorov-Smirnov testing, Storey q-values, weighted log-likelihood classification, classical clustering algorithms |
| Approximate | A standard method implemented through discretization, asymptotics, or reconstruction from summaries rather than raw observations | histogram-based Mann-Whitney, grid-based ECDF overlap, effective-sample-size KS p-values |
| Heuristic | Engineered ranking, thresholding, or aggregation rules chosen for workflow utility rather than statistical optimality | effect-size ranking, module scoring constants, pairwise control aggregation |
| External-service-backed | Results depend on external databases, APIs, or stored procedures whose internal logic is not fully versioned inside this repository | Enrichr, DisGeNET, Open Targets, Azure SQL stored procedures, LLM-assisted evidence synthesis |

That taxonomy is used throughout the book so the reader can tell where the pipeline is closest to a classical statistical method and where it is best interpreted as a reproducible engineering decision.

## Package Map

The complete package map with all four parts is in the "Reading This Book" section below. For quick orientation, Part I covers the mathematical and statistical algorithms in the following packages:

| Chapter | Primary packages | Role |
|---|---|---|
| [§ methylutils](chapters/01-methylutils.md#sec-methylutils) | `methylutils` | shared mathematical layer: centroids, ECDF views, testing, effect sizes, classifier densities |
| [§ methylcentroid](chapters/02-methylcentroid.md#sec-methylcentroid) | `methylcentroid` | centroid construction and coverage preprocessing |
| [§ methyldetector](chapters/03-methyldetector.md#sec-methyldetector) | `methyldetector` | DMP testing, filtering, classifier export, and production panel bypass |
| [§ methylclassifier](chapters/04-methylclassifier.md#sec-methylclassifier) | `methylclassifier` | binary, multi-chromosome, OvR, and multiclass learned-head classification |
| [§ methylpredictor validation](chapters/05-methylpredictor-and-validation.md#sec-methylpredictor-validation) | `methylpredictor`, `methylvalidation`, `methyldiseaseprogression` | prediction metrics, Monte Carlo validation, stability analysis, production freeze, progression synthesis |
| [§ methylmapper](chapters/07-methylmapper.md#sec-methylmapper) | `methylmapper` | DMP-to-gene mapping and gene-level aggregation |
| [§ methyldeconv](chapters/07a-methyldeconv.md#sec-methyldeconv) | `methyldeconv` | Houseman flat and HiTIMED hierarchical cell-type deconvolution (analyte-driven trees) |
| [§ methylenricher](chapters/08-methylenricher.md#sec-methylenricher) | `methylenricher` | over-representation analysis, pathway graph clustering, and module scoring |
| [§ methylalignmentqc](chapters/09-methylalignmentqc.md#sec-methylalignmentqc) | `methylalignmentqc` | deterministic QC parsing and summarization |
| [§ limitations](chapters/10-limitations-and-open-questions.md#sec-limitations) | all | caveats, legacy remnants, and claims that should be softened |

## Shared Data Model

At the lowest level the code works with per-position methylated and unmethylated counts. For a sample $s$ at genomic position $i$,

$$
x_{si} = \frac{m_{si}}{m_{si} + u_{si}},
$$

where $m_{si}$ is the methylated count and $u_{si}$ is the unmethylated count. The code clips or guards zero-coverage positions instead of leaving them undefined.

For a centroid with $N_i$ contributing samples at locus $i$, the shared sufficient statistics are

$$
N_i = \sum_s 1,\qquad
S_{x,i} = \sum_s x_{si},\qquad
S_{x^2,i} = \sum_s x_{si}^2.
$$

These yield the implemented centroid mean and unbiased sample variance:

$$
\hat\mu_i = \frac{S_{x,i}}{N_i},
\qquad
\hat\sigma_i^2 = \max\!\left(
\frac{S_{x^2,i} - S_{x,i}^2 / N_i}{\max(N_i - 1, 1)},
\varepsilon
\right),
$$

with a small numerical floor $\varepsilon$ used throughout the code to avoid degenerate densities or divisions by zero.

In the ECDF-centered path, each locus also stores a binned histogram

$$
\{c_{ib}\}_{b=1}^B
$$

over shared methylation bin edges $0 = e_0 < e_1 < \cdots < e_B = 1$. Those histograms drive both empirical distribution reconstruction and approximate rank-based testing.

## Shared Notation

| Symbol | Meaning |
|---|---|
| $x_{si}$ | methylation fraction for sample $s$ at locus $i$ |
| $m_{si}, u_{si}$ | methylated and unmethylated counts |
| $N_i, S_{x,i}, S_{x^2,i}$ | centroid sufficient statistics |
| $c_{ib}$ | count in histogram bin $b$ for locus $i$ |
| $F_i(x)$ | reconstructed cumulative distribution function at locus $i$ |
| $f_i(x)$ | reconstructed density, usually the derivative of a monotone spline |
| $D_i$ | two-sample KS statistic at locus $i$ |
| $U_i$ | Mann-Whitney $U$ statistic at locus $i$ |
| $q_i$ | Storey-adjusted q-value |
| $O_i$ | continuous overlap $\int_0^1 \min(f_{1i}, f_{2i})\,dx$ |
| $w_i$ | feature weight used by the classifier, usually derived from detector output |

## Cross-Cutting Assumptions

Several assumptions recur across the repository:

- Loci are treated as conditionally independent inside the classifier score, even though methylation along the genome is correlated.
- Multiple-testing corrections are applied across many dependent loci and, downstream, across aggregated genes. This is standard practice but should not be oversold as exact finite-sample control.
- Some packages operate on summaries rather than raw per-read data. That preserves scalability, but it changes the interpretation of rank tests and distribution overlap.
- Downstream packages use external services and curated priors. Those are part of the workflow, but they are not internal statistical estimators.

## Reading This Book

This book is organized into two parts (mathematical foundations and workflow/configuration theory). CLI runbooks, deployment, and exhaustive parameter tables were moved to the **Usage** and **Reference** pillars during the 2026-06 documentation IA revision.

**Part I: Mathematical and Statistical Foundations** covers the algorithms and estimators in order from the lowest shared layer to the downstream applications. Readers interested in the production classifier should start with [§ methylutils](chapters/01-methylutils.md#sec-methylutils), then continue through [§ methylcentroid](chapters/02-methylcentroid.md#sec-methylcentroid), [§ methyldetector](chapters/03-methyldetector.md#sec-methyldetector), and [§ methylclassifier](chapters/04-methylclassifier.md#sec-methylclassifier). Readers interested in biological interpretation should continue to [§ methylmapper](chapters/07-methylmapper.md#sec-methylmapper), [§ methyldeconv](chapters/07a-methyldeconv.md#sec-methyldeconv), and [§ methylenricher](chapters/08-methylenricher.md#sec-methylenricher). [§ limitations](chapters/10-limitations-and-open-questions.md#sec-limitations) is intentionally blunt and should be read before any publication claim is finalized.

**Part II: Configuration Semantics and Workflow Theory** documents study manifest anatomy ([§ project configuration](chapters/11-project-configuration.md#sec-project-configuration)), the two operational workflow models ([§ two workflows](chapters/12-two-workflows.md#sec-two-workflows)), and model creation/validation theory ([§ model creation validation](chapters/15-model-creation-and-validation.md#sec-model-creation-validation)).

**Operator commands and deployment** live in the [Usage manual](../usage/index.md). **Parameter lookup** lives in [Reference: configuration-reference](../reference/configuration-reference.md). **Implementation internals** live in [Implementation guide](../implementation/index.md).

### Package Map

| Part | Chapter | Primary packages | Role |
|---|---|---|---|
| I | [§ methylutils](chapters/01-methylutils.md#sec-methylutils) | `methylutils` | shared mathematical layer |
| I | [§ methylcentroid](chapters/02-methylcentroid.md#sec-methylcentroid) | `methylcentroid` | centroid construction |
| I | [§ methyldetector](chapters/03-methyldetector.md#sec-methyldetector) | `methyldetector` | DMP testing and panel export |
| I | [§ methylclassifier](chapters/04-methylclassifier.md#sec-methylclassifier) | `methylclassifier` | classification heads |
| I | [§ methylpredictor validation](chapters/05-methylpredictor-and-validation.md#sec-methylpredictor-validation) | `methylpredictor`, `methylvalidation`, `methyldiseaseprogression` | validation and freeze theory |
| I | [§ methylmapper](chapters/07-methylmapper.md#sec-methylmapper) | `methylmapper` | DMP-to-gene mapping |
| I | [§ methyldeconv](chapters/07a-methyldeconv.md#sec-methyldeconv) | `methyldeconv` | Houseman / HiTIMED cell deconvolution |
| I | [§ methylenricher](chapters/08-methylenricher.md#sec-methylenricher) | `methylenricher` | enrichment and modules |
| I | [§ methylalignmentqc](chapters/09-methylalignmentqc.md#sec-methylalignmentqc) | `methylalignmentqc` | QC parsing |
| I | [§ limitations](chapters/10-limitations-and-open-questions.md#sec-limitations) | all | caveats and legacy |
| II | [§ project configuration](chapters/11-project-configuration.md#sec-project-configuration) | `methylutils` (ProjectConfig) | study manifest semantics |
| II | [§ two workflows](chapters/12-two-workflows.md#sec-two-workflows) | all | workflow theory |
| II | [§ model creation validation](chapters/15-model-creation-and-validation.md#sec-model-creation-validation) | `methylvalidation` | model lifecycle theory |
