# MethylCentroid {#sec-methylcentroid}
## Role

`methylcentroid` is the package that turns a cohort of samples into a reusable centroid object. The heavy mathematical lifting is delegated to `methylutils`, but `methylcentroid` fixes the operational semantics of what a centroid means in this repository:

- which samples are aggregated,
- how coverage outliers are handled,
- which sufficient statistics are persisted, and
- how the resulting HDF5 artifacts are organized for downstream steps.

The main implementation lives in `packages/methylcentroid/methyl_centroid/methyl_centroid.py`, with accumulation handled by `methyl_utils/core/centroid_builder.py`.

## What A Centroid Stores

Each persisted centroid contains:

- genomic positions,
- context metadata,
- the sufficient statistics $N_i$, $S_{x,i}$, $S_{x^2,i}$,
- count summaries $S_{m,i}$, $S_{u,i}$, $S_{c^2,i}$, $S_{wx^2,i}$, and
- per-locus histogram counts used to reconstruct ECDFs.

That is why the centroid is more than a mean profile. It is a compressed empirical-distribution object designed to support both detector-side testing and classifier-side scoring.

## Sample-To-Centroid Update Rule

Given one sample with per-locus methylation fraction

<div id="eq-centroid-update" markdown="1">

$$
x_{si} = \frac{m_{si}}{m_{si} + u_{si}},
$$

the centroid builder updates

$$
N_i \leftarrow N_i + 1,\qquad
S_{x,i} \leftarrow S_{x,i} + x_{si},\qquad
S_{x^2,i} \leftarrow S_{x^2,i} + x_{si}^2.
$$

</div>

It also updates the count-derived quantities

<div id="eq-centroid-count-update" markdown="1">

$$
S_{m,i} \leftarrow S_{m,i} + m_{si},\qquad
S_{u,i} \leftarrow S_{u,i} + u_{si},
$$

$$
S_{c^2,i} \leftarrow S_{c^2,i} + (m_{si} + u_{si})^2,\qquad
S_{wx^2,i} \leftarrow S_{wx^2,i} + \frac{m_{si}^2}{m_{si}+u_{si}},
$$

</div>

and increments the appropriate methylation histogram bin for $x_{si}$.

These updates make the centroid a compact empirical summary rather than a full sample-by-locus matrix.

## Coverage Capping By Binomial Thinning

One of the most important preprocessing choices in this package is optional coverage capping. The code implements binomial thinning:

1. for any locus with coverage $n_i = m_i + u_i > n_{\text{cap}}$,
2. compute $p_i = n_{\text{cap}} / n_i$,
3. draw

<div id="eq-binomial-thinning" markdown="1">

$$
m_i' \sim \operatorname{Binomial}(m_i, p_i),
\qquad
u_i' \sim \operatorname{Binomial}(u_i, p_i).
$$

</div>

This preserves the methylation fraction in expectation while reducing the influence of ultra-deep loci. In other words, it is a variance-control and robustness device, not a biological model.

The implementation exposes both manual capping and auto-capping heuristics. The thinning itself is principled; the choice of $n_{\text{cap}}$ is still a workflow decision.

## Auto-Cap Heuristic

The package documentation describes automatic coverage capping through IQR-style heuristics computed from sampled positions. That should be interpreted as an engineering rule:

$$
n_{\text{cap}} \approx Q_3 + k\cdot \operatorname{IQR},
$$

with implementation-specific sampling and fallbacks. This is useful operationally, but it is not a statistically identified parameter.

## Why Histograms Matter

The centroid builder stores histogram counts even though the mean and variance are already available. That is because the downstream detector and classifier do not rely only on moments. They reconstruct empirical distributions from the binned counts and then use those reconstructed distributions for overlap calculations, KS testing, and classification.

This makes `methylcentroid` a crucial bridge package: it is the step where sample-level methylation is transformed into an empirical-distribution object that later packages can reuse without reloading the full cohort.

## Optional M-value residualization {#sec-centroid-mvalue-residual}

The shipped centroid path uses raw \(x_{si}=m_{si}/(m_{si}+u_{si})\). An **opt-in** transform, used only when `residualizeCoefDir` is bound, residualizes M-values against label-free covariates and maps residuals back to \((0,1)\) before updating \(S_x\), \(S_{x^2}\), and histogram bins. Raw \(S_m\)/\(S_u\) are unchanged. Absent coefficients, the builder is bit-identical to today.

See [M-value residualization implementation](../../implementation/mvalue-residualization.md) for each confounder (Hannum 2013 age score, smoking, BMI, CRP, Ω ALR). Isolation and leakage rules: [Buffy M-value residualization](../../research/buffy-mvalue-residualization.md). This lives in a **forked** procedure rather than the default methylation lifecycle.

## Publication Guidance

For publication text, the safest description is:

- `methylcentroid` constructs empirical per-locus cohort summaries,
- it preserves both moments and histogram information,
- it optionally reduces extreme coverage through binomial thinning, and
- it is upstream of all ECDF-based detector and classifier logic.

The package should **not** be described as fitting a parametric methylation model. It is a summary constructor for the nonparametric empirical-distribution path.
