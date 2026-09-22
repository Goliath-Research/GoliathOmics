# MethylDeconv {#sec-methyldeconv}
## Role

`methyldeconv` estimates the cell-type composition $\Omega$ of each sample from its methylation profile and writes those fractions as model covariates. It runs as the `pipeline.cell_deconvolution` action after the freeze mapper stage (see [§ two workflows](12-two-workflows.md#sec-two-workflows)) and feeds the ECDF second-stage stacker described in the [end-to-end workflow](../../architecture/end-to-end-workflow.md).

The action exposes a `method` switch with two estimators that share one output contract:

- **`houseman`** (default) — a single flat constrained projection against a reference blood basis.
- **`hitimed`** — an analyte-driven hierarchical tree that reuses the same per-node projection.

Relevant code:

- `packages/methyldeconv/methyl_deconv/core/houseman.py` — flat projection and marker extraction
- `packages/methyldeconv/methyl_deconv/core/hitimed.py` — hierarchical solver
- `packages/methyldeconv/methyl_deconv/config.py` — `CellDeconvStepConfig` / `CellDeconvRuntimeParams`
- `packages/methyldeconv/methyl_deconv/core/runner.py` — method dispatch and CSV/manifest emission

## Flat Houseman Projection

For a sample with observed methylation vector $y \in [0,1]^{n}$ at the reference marker loci and a reference basis matrix $M \in [0,1]^{n \times K}$ (markers $\times$ $K$ cell types), the fractions solve a constrained least-squares (constrained projection) problem:

<div id="eq-houseman-qp" markdown="1">

$$
\hat\Omega = \arg\min_{\Omega} \; \lVert y - M\Omega \rVert_2^2
\quad\text{s.t.}\quad \Omega_k \ge 0, \;\; \sum_{k=1}^{K}\Omega_k = 1 .
$$

</div>

This is the classical reference-based deconvolution of Houseman et al. The implementation (`houseman_qp`) solves [Eq. houseman-qp](#eq-houseman-qp) with SciPy SLSQP on the host (the number of cell types $K$ is small), optionally touching a CuPy device for large marker panels. The default reference is the wheel-packaged `flowsorted_blood_epic_idol_v1.json` IDOL basis with $K = 6$ immune types (`CD8T, CD4T, NK, Bcell, Mono, Neu`).

`extract_marker_vector` builds $y$ and a coverage mask by reading each sample's per-chromosome `*.h5` files at the basis coordinates, keeping only markers with coverage $\ge$ `marker_min_coverage`. When the observed fraction of markers falls below `min_marker_fraction`, the row is emitted as `NaN` with `qp_status = insufficient_markers` rather than a fabricated estimate.

## HiTIMED Hierarchical Deconvolution

HiTIMED replaces the single projection with a **tree of small projections**. Each internal node splits a parent compartment into its ordered children using a node-specific marker basis, and each split is solved by the same [Eq. houseman-qp](#eq-houseman-qp). Child masses are multiplied down each path so the collected leaf proportions again sum to 1.

Let a node $v$ have children $c_1,\dots,c_{m}$ and node basis $M_v$. Solving [Eq. houseman-qp](#eq-houseman-qp) at $v$ against the observed markers yields split weights $\omega^{(v)}$. For a leaf $\ell$ reached by path $v_0 \to v_1 \to \cdots \to \ell$, its proportion is the product of split weights along the path,

<div id="eq-hitimed-path" markdown="1">

$$
\Omega_\ell = \prod_{t} \omega^{(v_t)}_{c_{t+1}},
$$

</div>

renormalized across leaves. This is not new mathematics: the estimator is a composition of constrained projections, each identical in form to flat Houseman.

**Node fallback.** If a node cannot be solved (fewer observed markers than children, or observed fraction below `min_marker_fraction`), its parent mass is split evenly across that node's children and the row is flagged `qp_status = partial`. If the *root* itself cannot be solved, the row is `NaN` with `qp_status = insufficient_markers`.

## Analyte-Driven Trees

Which tree root is used is selected by the sample **analyte**, resolved from the `analyte` config field (defaulting to the study `regulatory.primary_analyte`) through the basis `analyte_trees` map. All three covered analytes share one immune subtree; only the top layer differs:

| Analyte | Tree root | Leaf columns | Rationale |
|---------|-----------|--------------|-----------|
| `buffy_coat` | immune subtree | immune leaves only | Blood has no tumor DNA; resolve leukocyte composition. |
| `cfdna` | plasma top split (`tumor` vs `non_tumor`) | `tumor_fraction` + immune leaves | Plasma is mostly hematopoietic background with a low ctDNA fraction; the tumor child is a single lumped leaf, exposing ctDNA burden as a covariate. |
| `tissue` | full tumor / immune / stromal tree | tumor + immune + stromal leaves | Solid tissue carries a substantial tumor/stromal compartment above the immune subtree. |

Leaf order is deterministic depth-first from the analyte root, so the `cell_fractions.csv` column set is stable per analyte.

## Output Contract

Both methods write `{output_base}/cell_fractions/cell_fractions.csv` plus `cell_fractions.manifest.json`. The manifest records `method`; HiTIMED additionally records `analyte` and `tree_root`. HiTIMED only *grows* the column set relative to Houseman, so the ECDF/tabular `covariates_path` contract in profiles is unchanged (see [§ project configuration](11-project-configuration.md#sec-project-configuration)). The `group` and `qp_status` columns are excluded from covariate auto-inference to guard against label leakage.

## Method and Category Labels

Following the taxonomy in [§ limitations](10-limitations-and-open-questions.md#sec-limitations):

- Both Houseman and HiTIMED are **principled** constrained-projection estimators; HiTIMED is a deterministic composition of the same projection and introduces no Monte Carlo step.
- The **reference bases** are an external dependency. The measured blood immune subtree (derived from the IDOL basis) ships in the wheel, but the cfDNA plasma top split and the full tissue tumor/stromal tree are composed **offline** from operator-supplied atlases and are not fabricated at worker runtime.

## Configuration

`actionConfig.cell_deconvolution` (site/profile) sets the tunable knobs, with `default=None` for operator-set values (no Python fallbacks):

| Field | Meaning |
|-------|---------|
| `method` | `houseman` (default) or `hitimed` |
| `seed_basis_path` | flat IDOL basis override (`houseman`) |
| `hierarchy_basis_path` | hierarchical v2 basis override (`hitimed`) |
| `analyte` | tree selector; defaults to `regulatory.primary_analyte` |
| `contexts`, `marker_min_coverage`, `min_marker_fraction` | marker extraction and QP gating |

The shipped `cell_deconv_hitimed.profile.json` profile sets `method = hitimed` with ALR composition covariates (and `tumor_fraction` for cfDNA). Design and provisioning details: [`docs/plans/hitimed-hierarchical-deconvolution.plan.md`](../../plans/hitimed-hierarchical-deconvolution.plan.md).
