# Cohort tree configuration (MethylUtils)

This document describes how **control/disease** project JSON models cohorts for centroids, detectors, classifiers, validation, and prediction. It applies to [`pipeline_config.py`](../methyl_utils/pipeline_config.py) (`ProjectConfig`, `GroupConfig`).

## Resolved leaves (what the pipeline actually runs)

The pipeline always works with a **flat ordered list** of cohorts: `(label, sample_paths)` from `get_resolved_groups()` (control groups first, then disease). **Centroid directories, comparison pairs, OvR `class_names`, and Monte Carlo cohort rows** all refer to these **leaf labels**, not to internal grouping nodes.

## `diseases.groups[].stages` — semantics (v1)

The JSON field is named **`stages`** for historical and example reasons (cancer type × **stage**), but it means **“children of this disease family”**: any set of **mutually exclusive enrollment bins** you want separate centroids for.

Examples of valid child labels (same mechanism):

- TNM or clinical stage: `I`, `II`, `III`, `IV`
- PCa-style bins already in your study: `pca1`, `pca2`, …
- Breast or other tumor **molecular / receptor** strata: `HR_pos_HER2_neg`, `TNBC`, …
- Other axes (grade band, treatment arm) if each bin has its own `sample_paths`

Rules enforced by the model:

- If **`stages`** is set on a disease group, the **parent must not** set `sample_paths` (only children carry samples).
- Each child must define **`sample_paths`** (non-empty after resolution).
- **Nested `stages` under a child are not supported in v1** (one parent → one child list only).

Optional **`description`** on each `GroupConfig` (including each stage child) is a short human-readable meaning (e.g. clinical stage narrative) for reporting and tools such as readiness Grok review; keep it free of file paths.

Resolved leaf names are **`{parent.label}_{child.label}`** (e.g. `pca_pca1`, `breast_HR_pos_HER2_neg`). The **scientific meaning** of the child is entirely in your **`label` strings** and cohort definitions, not in the key name `stages`.

## `cohort_hierarchy` and reporting

If you omit **`cohort_hierarchy`**, the project may **autofill** a minimal v1 structure when disease groups use **`stages`**: `control_strata`, `disease_families` with `stage_labels` and `leaves`. That metadata is used for **hierarchy-aware summaries** (e.g. MethylPredictor `hierarchy_summary`) on top of the same **flat** probability vector over leaves.

You can still use **multiple top-level** `diseases.groups` entries (e.g. one family for prostate with stage-like children, another for breast with receptor-like children) without changing the schema.

## Comparisons

- **`control_vs_each_disease`**: first resolved control vs each resolved disease leaf (typical with one control pool).
- **`all_pairs`**: every resolved control × every resolved disease leaf (default when **multiple** control groups and `comparisons` omitted).
- Explicit **`comparisons`** list: must use **resolved** `control_group` / `disease_group` labels as in `get_comparisons()`.

For background on OvR path resolution from these comparisons, see the MethylClassifier [CONFIG_FILE_GUIDE.md](../../methylclassifier/CONFIG_FILE_GUIDE.md) (project JSON / `ovr_binary_pickles_from_comparisons`).

---

## Appendix: v2 spike — recursive trees (not implemented)

**Status:** Design only. v1 (flat leaves + optional single `stages` level) remains the supported configuration.

**Motivation:** Some studies need **more than two** logical levels (e.g. cancer → subtype → stage) or **symmetric nested controls** (site → ancestry → age band) without flattening everything into top-level `groups`.

**Proposed principles:**

1. **Recursive node type** (e.g. `children` alongside or instead of `stages`): internal nodes have **no** `sample_paths`; **only leaves** have samples (same invariant as v1 parent/child).
2. **Flattening rule:** depth-first (or documented stable order) walk produces the same **`get_resolved_groups()`** list; leaf label = joined path e.g. `breast_HRneg_III` (exact separator TBD).
3. **`cohort_hierarchy` / `cohort_tree_dict`:** evolve from “family + stage_labels” to a small **tree** (nodes with `id`, `parent`, `leaf_index` or list of descendant leaf ids) so reporting can aggregate at arbitrary internal nodes.
4. **OvR / MethylPredictor:** unchanged at the math layer — still one **flat** multiclass simplex over leaves unless a separate project adds a hierarchical PKL bundle.

**Code touchpoints if implemented later:** [`pipeline_config.py`](../methyl_utils/pipeline_config.py) (`_expand_side_groups`, `autofill_cohort_hierarchy`, `get_comparisons`), MethylCentroid/MethylDetector path resolvers, MethylValidation `project_gen.py`, MethylClassifier `project_resolver.py`, MethylPredictor `project_resolver.py` / `cohort_hierarchy` consumption.
