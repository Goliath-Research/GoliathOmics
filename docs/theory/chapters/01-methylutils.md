# MethylUtils {#sec-methylutils}
## Scope

`methylutils` is the mathematical spine of the repository. The core implementations live in:

- `methyl_utils/core/centroid_builder.py`
- `methyl_utils/core/distribution_views.py`
- `methyl_utils/methyl_centroid_pair.py`
- `methyl_utils/statistical_tests.py`
- `methyl_utils/ecdf_classifier.py`

Those modules define the empirical-distribution view used by centroid comparison, DMP detection, and ECDF classification. They also expose several auxiliary or legacy methods, especially beta-based distances, that remain available as optional utilities.

## Implemented Method Inventory

| Area | Main code path | Status in this book |
|---|---|---|
| centroid sufficient statistics | `core/centroid_builder.py` | principled |
| ECDF reconstruction from histograms | `core/distribution_views.py` | principled numerical representation |
| continuous overlap integral | `statistical_tests.py` | approximate numerical integration |
| KS testing on reconstructed ECDFs | `statistical_tests.py` | asymptotic and grid-based |
| Mann-Whitney from binned counts | `statistical_tests.py` | approximate because it reconstructs rank information from histograms |
| Storey q-values | `statistical_tests.py` | principled, with standard tuning caveats |
| DerSimonian-Laird style heterogeneity | `statistical_tests.py` | principled method-of-moments adaptation |
| canonical biological effect size | `statistical_tests.py` | heuristic ranking score |
| ECDFClassifier | `ecdf_classifier.py` | principled scoring rule plus heuristic stabilizers |
| beta distances and overlaps | `metrics_core.py` | auxiliary; not the main detector-classifier path |

## Centroid Sufficient Statistics

For each locus $i$, the builder stores per-sample methylation fractions $x_{si}$ together with aggregate statistics:

<div id="eq-centroid-moments" markdown="1">

$$
\hat\mu_i = \frac{S_{x,i}}{N_i},
\qquad
\hat\sigma_i^2 = \max\!\left(
\frac{S_{x^2,i} - S_{x,i}^2 / N_i}{\max(N_i - 1, 1)},
\varepsilon
\right),
$$

</div>

where $N_i$ is the number of samples contributing to that locus and $\varepsilon$ is a small numerical floor.

The centroid builder also stores count-level summaries:

<div id="eq-count-summaries" markdown="1">

$$
S_{m,i} = \sum_s m_{si},\qquad
S_{u,i} = \sum_s u_{si},\qquad
S_{c^2,i} = \sum_s (m_{si}+u_{si})^2,\qquad
S_{wx^2,i} = \sum_s \frac{m_{si}^2}{m_{si}+u_{si}},
$$

</div>

which are later reused by the heterogeneity calculation. Finally, each locus accumulates histogram counts $c_{ib}$ over shared bin edges $\{e_b\}$, producing the `binned_stats` object that drives both ECDF reconstruction and histogram-based testing.

## ECDF Reconstruction

The code constructs a cumulative distribution at bin edges by normalizing cumulative bin counts:

<div id="eq-ecdf-edges" markdown="1">

$$
\tilde F_i(e_b) =
\frac{\sum_{r \le b} c_{ir}}{\sum_{r=1}^{B} c_{ir}},
\qquad b = 0,\dots,B.
$$

</div>

`ECDFView` then interpolates these edge values with a monotone PCHIP spline [fritsch1980]. The resulting representation supplies:

- a continuous cumulative distribution $\tilde F_i(x)$,
- a continuous density proxy $\tilde f_i(x) = \max(\partial_x \tilde F_i(x), \varepsilon)$, and
- numerically stable means and variances via the stored sufficient statistics.

This is an empirical-distribution model, not a beta or Gaussian fit. That distinction is central to the detector-classifier path.

## Two-Sample Comparison

### KS Statistic on Reconstructed ECDFs

For aligned centroid pairs, the code evaluates the two-sample Kolmogorov-Smirnov statistic

<div id="eq-ks" markdown="1">

$$
D_i = \sup_{x \in [0,1]} \left| \tilde F_{1i}(x) - \tilde F_{2i}(x) \right|.
$$

</div>

Implementation detail matters here: the supremum is approximated on a uniform grid rather than computed analytically, and the $p$-value is obtained from the large-sample Smirnov distribution [@kolmogorov1933; @feller1948] using the harmonic mean effective sample size

<div id="eq-ks-pvalue" markdown="1">

$$
n_{\text{eff},i} =
\frac{2}{1/\max(n_{1i},1) + 1/\max(n_{2i},1)},
\qquad
p_i = \Pr\!\left(K \ge \sqrt{n_{\text{eff},i}}\,D_i\right).
$$

</div>

This is a reasonable screening approximation, but it is not an exact finite-sample KS test on raw observations.

### Mann-Whitney from Histogram Counts

The repository also implements a vectorized Mann-Whitney calculation directly from centroid histograms. Let $c^{(1)}_{ib}$ and $c^{(2)}_{ib}$ be the two groups' bin counts at locus $i$. The code reconstructs the $U$ statistic as

<div id="eq-mwu-bins" markdown="1">

$$
U_i =
\sum_{b=1}^{B}
c^{(1)}_{ib}
\left(
\sum_{r < b} c^{(2)}_{ir}
+ \frac{1}{2} c^{(2)}_{ib}
\right).
$$

</div>

This is followed by the usual normal approximation,

<div id="eq-dl" markdown="1">

$$
\mu_{U,i} = \frac{n_{1i} n_{2i}}{2},
\qquad
Z_i = \frac{U_i - \mu_{U,i}}{\sqrt{\operatorname{Var}(U_i)}},
$$

with a tie correction derived from the combined histogram counts. The important caveat is that the code does **not** reconstruct the full rank ordering of raw methylation values; it reconstructs a rank-like summary from histogram bins. In this book that is treated as an approximation to the classical Mann-Whitney test [mann1947], not as an identical procedure.

### Heterogeneity

`dl_heterogeneity()` adapts the DerSimonian-Laird method-of-moments estimator [dersimonian1986] to the centroid summaries:

$$
\theta_i = \frac{S_{m,i}}{S_{m,i} + S_{u,i}},
\qquad
Q_i = S_{wx^2,i} - \frac{S_{m,i}^2}{S_{m,i} + S_{u,i}},
$$

$$
\tau_i^2 =
\max\!\left(
0,\,
\frac{Q_i - \max(N_i - 1, 0)}
{\max\!\left((S_{m,i}+S_{u,i}) - S_{c^2,i}/(S_{m,i}+S_{u,i}), \varepsilon\right)}
\right).
$$

</div>

This is a useful heterogeneity diagnostic, but it should be described as a method-of-moments adaptation to stored centroid summaries, not as a full random-effects model fit.

## Continuous Overlap

Once densities have been reconstructed, the code measures shared support through

<div id="eq-overlap" markdown="1">

$$
O_i = \int_0^1 \min\!\left(\tilde f_{1i}(x), \tilde f_{2i}(x)\right)\,dx.
$$

</div>

Numerically, the integral is evaluated on a dense grid after each density is renormalized by trapezoidal integration. This makes the overlap robust to small interpolation drift, but it remains a grid approximation.

## Multiple Testing and p-Value Aggregation

For genome-wide DMP screening, `storey_qvalues()` estimates the null fraction $\pi_0$ across a grid of $\lambda$ values and then computes monotone q-values [storey2003]:

<div id="eq-storey" markdown="1">

$$
\hat\pi_0(\lambda) =
\frac{\#\{p_j > \lambda\}}{(1-\lambda)m},
\qquad
\hat\pi_0 = \min\!\left(1, \operatorname{median}_{\lambda} \hat\pi_0(\lambda)\right),
$$

$$
q_{(i)} = \min_{j \ge i} \frac{\hat\pi_0\,m\,p_{(j)}}{j}.
$$

</div>

The same module also provides Fisher [fisher1925] and Stouffer [stouffer1949] style p-value combination utilities. Those are not central to the detector itself, but downstream packages reuse them conceptually for gene-level aggregation.

## Canonical Biological Effect Size

The repository separates statistical significance from a biological ranking score. The implemented canonical effect size is

<div id="eq-effect-size" markdown="1">

$$
\operatorname{effect}_i =
|\Delta\mu_i|\,
(1 - O_i)\,
\exp\!\left[-\lambda_{\mathrm{var}}
\left(\sqrt{\sigma_{1i}^2} + \sqrt{\sigma_{2i}^2}\right)\right].
$$

</div>

An optional mean-level weight $g(\bar\mu_i)$ can be applied, with the code supporting square-root, linear, saturating, and boundary-threshold variants.

This score is useful operationally, but it is not a test statistic, likelihood ratio, or posterior quantity. It is a deliberately engineered ranking rule. Publication text should therefore describe it as a heuristic biological prioritization score.

## ECDFClassifier

The classifier in `ecdf_classifier.py` turns the centroid histograms into per-class densities and scores a sample $x = (x_1,\dots,x_p)$ via a weighted mean log-density:

<div id="eq-ecdf-score" markdown="1">

$$
\operatorname{score}_k(x) =
\frac{\sum_{i=1}^{p} w_i \log f_{ki}(x_i)}
{\sum_{i=1}^{p} w_i}.
$$

</div>

The class probabilities are then computed by temperature-scaled softmax:

<div id="eq-ecdf-softmax" markdown="1">

$$
P(Y = k \mid x)
\propto
\exp\!\left(\frac{\operatorname{score}_k(x)}{T_{\text{eff}}}\right),
\qquad
T_{\text{eff}} = T \sqrt{n_{\text{effective}}},
$$

</div>

where $n_{\text{effective}} = (\sum_i w_i)^2 / \sum_i w_i^2$ is the inverse-Simpson effective number of weighted loci.

Two stabilizers are important because they materially affect behavior:

1. log-densities are floored before averaging, so extremely small density values cannot dominate the score, and
2. unavailable loci are removed from the denominator by setting their effective weights to zero.

The result is a practical Naive Bayes style classifier [domingos1997], but it is not a fully calibrated generative posterior unless the independence and density assumptions actually hold. Optional Platt scaling [platt1999] may further recalibrate the binary probabilities after the ECDF score has been computed.

## Auxiliary Beta-Based Metrics

`metrics_core.py` still contains exact beta divergences, beta overlap surrogates, and moment calculations. A legacy beta-comparator module (`methyl_utils/comparison.py`) was removed from the active package tree; beta-distance utilities in `metrics_core.py` remain for auxiliary analysis only. They are **not** the main production path for DMP detection plus classification and are not used by the active detector/classifier runtime flow. In publication text, beta distances should therefore be described as an auxiliary branch of the repository, not as the canonical detector-classifier model.
