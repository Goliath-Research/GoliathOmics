# MethylPredictor Usage Guide

## Overview

MethylPredictor runs a trained **MethylClassifier** on two test sample sets—**control** (e.g. healthy) and **disease** (e.g. sick)—and reports **accuracy metrics** (accuracy, balanced accuracy, sensitivity, specificity, F1, confusion matrix, etc.). It does not train the model; it evaluates it on holdout test samples.

There are **two ways to run MethylPredictor**:

1. **Docker container** — Run inside the MethylPipeline container with dependencies provided.
2. **Local host with virtual environment** — Create a venv, install MethylUtils, MethylClassifier, and MethylPredictor, activate the venv, and run on the host.

Use one or the other; the CLI and behavior are the same once the environment is active.

---

## Setup 1: Docker container

Use this when you want a reproducible environment (e.g. same as MethylCentroid/MethylDetector/MethylClassifier).

**Prerequisites:** Docker; for GPU (if used), NVIDIA Container Toolkit.

**1. Start the container**

From the MethylPipeline repo:

```bash
cd /path/to/MethylPipeline/docker
docker compose up -d
```

(Use the same container name as in your compose file; below assumes `methylpipeline` and repo mounted at `/workspace`.)

**2. Run MethylPredictor**

Paths in your config or arguments must be valid **inside** the container (e.g. `/workspace/...`). From the host:

```bash
docker exec -w /workspace methylpipeline \
  methyl-predictor --project /workspace/configs/project.json
```

Or with explicit model and test sets:

```bash
docker exec -w /workspace methylpipeline \
  methyl-predictor --model-dir /workspace/classifiers/PCa_vs_Healthy \
    --output-dir /workspace/out \
    --test-control /workspace/test/control.csv \
    --test-disease /workspace/test/disease.csv
```

**Common options:** `--project`, `--config`, `--model`, `--model-dir`, `--output-dir`, `--test-control`, `--test-disease`, `--step-override`, `--per-comparison`, `--debug`. Paths must be container paths (e.g. `/workspace/...`).

---

## Setup 2: Local host with virtual environment

Use this when you run on the host and want to activate a virtual environment before running MethylPredictor.

**Prerequisites:** Python 3.10-3.14.

**1. Create a virtual environment** (from the **MethylPipeline repository root**; canonical name is `.venv`)

```bash
cd /path/to/MethylPipeline
python3.14 -m venv .venv
```

**2. Activate the virtual environment**

```bash
source .venv/bin/activate
```

On Windows: `.venv\Scripts\activate`. After activation, the prompt usually shows `(.venv)`.

**3. Install MethylUtils (required by MethylClassifier)**

MethylPredictor depends on MethylClassifier, which depends on MethylUtils. Install MethylUtils first:

```bash
cd /path/to/MethylPipeline/packages/methylutils
pip install -e .
```

**4. Install MethylClassifier**

```bash
cd /path/to/MethylPipeline/packages/methylclassifier
pip install -e .
```

**5. Install MethylPredictor**

```bash
cd /path/to/MethylPipeline/packages/methylpredictor
pip install -e .
```

**6. Run MethylPredictor**

With the virtual environment **activated** (`source .venv/bin/activate` from the repo root), use the CLI. Paths are on the **host**; you do not use a container.

```bash
methyl-predictor --project configs/project.json
methyl-predictor --model-dir /path/to/classifiers/PCa_vs_Healthy \
  --output-dir ./out \
  --test-control test/control.csv \
  --test-disease test/disease.csv
```

---

## Saved classifier vs raw detector directory

You **do not** need to “build” a new aggregated classifier every time you predict.

- **After MethylClassifier** (training/validation run), **`save_classifier_path`** (or `project_name` defaults) writes **one** artifact:
  - **Multiclass OvR**: a portable **`ecdf_one_vs_rest`** dict PKL — the K binary experts and union DMP layout are **fixed at save/export time** (`--export-ovr-pkl` does the same without classifying).
  - **Multi-chromosome binary**: a pickled **`MethylClassifier`** that already contains **all** chromosome sub-classifiers and (when fitted) chromosome weights.

**MethylPredictor** should use **`model_path`** to that file. For comparison projects, the resolver picks **`actionConfig.predictor.multiclass_model_path`** or **`model_path`** if set; otherwise **`classifiers/multiclass-classifier.pkl`** (native histogram / learned multiclass from MethylDetector) **if that file exists**; otherwise **`classifier.save_classifier_path`**, then the **OvR ECDF bundle** under **`classifiers/<control>/`**, then **`{project_name}-classifier.pkl`**. Native **`multiclass-classifier.pkl`** wins over the OvR bundle when both are present so detector-built models are not shadowed.

**`model_dir`** (a folder of `classifier-{chrom}*.pkl`) is still valid — typically MethylDetector output — but each predictor process **reloads every chromosome pickle** and wires multi-chromosome mode again. That is **more I/O and setup** than loading a **single saved PKL**; use **`model_path`** once the classifier step has produced it.

---

## Specifying test samples (healthy and disease groups)

MethylPredictor needs two sets of sample paths: **control** (expected class 0) and **disease** (expected class 1). Each sample is a directory that MethylClassifier can load.

### Profile `actionConfig.predictor` (study manifest + merged profile)

Use the **same shape** as top-level `controls` / `diseases`: each side has optional `label` and a **`groups`** array. Each group has **`label`** and **`sample_paths`** (CSV list files and/or directories, same as training). These keys live in profile **`actionConfig.predictor`**, not in the study manifest.

- If **`predictor.controls`** or **`predictor.diseases`** is missing, or has an empty **`groups`** list, that side is taken from the **root** project `controls` / `diseases` (so you can use `"predictor": {}` to validate on the **same cohorts** as training).
- For **per-comparison** projects, each run uses the comparison’s **`control_group`** / **`disease_group`** labels to pick the matching subgroup from those nested groups (see [`configs/project_Healthy_vs_PCa1-4.json`](../../configs/project_Healthy_vs_PCa1-4.json)).

### Hierarchical panel readout (`predictor.panel`)

For **multiclass OvR ECDF bundles** fused with **pairwise max-contrast** (the default when the control head is an aggregate over pairwise detectors), you can attach a **panel spec** so each sample gets an interpretable readout: **`healthy`**, **`primary_disease`** (top disease class belongs to your primary family), **`alternative_panel_disease`** (top disease is in another named family on the same panel), or **`indeterminate`** (no usable logits or top-two margin below a threshold).

Add under profile **`actionConfig.predictor`** (or the equivalent field on a standalone predictor config):

```json
"panel": {
  "primary_family": "prostate",
  "families": {
    "prostate": ["pca_stage1", "pca_stage2"],
    "colorectal": ["crc_a"]
  },
  "indeterminate_delta": 0.25
}
```

- **`families`** must **partition** every **disease** `class_name` from the model (all entries in `class_names[1:]`, each exactly once). Keys are arbitrary family labels; **`primary_family`** must be one of those keys.
- **`indeterminate_delta`** is optional (default **0.25**): when the winning column is a disease head, if **(best logit − second logit) < delta**, the sample is **`indeterminate`**.
- **Not supported** for native multiclass PKLs, non-OvR mode, or legacy **multi-chromosome `model_dir`** layouts in the classifier (the predictor passes the spec through; unsupported paths log a warning and skip panel columns).

When supported, prediction outputs gain **CSV columns** such as **`panel_label`**, **`panel_code`**, **`panel_logit_control`**, **`panel_logit_family_<name>`**, and a sidecar **`panel_report.json`** next to the predictions table. Implementation: `methyl_classifier.core.panel_fusion`.

### Blind samples (no known class)

Use **`predictor.blind`** when you only want **probabilities per class/subgroup** and **no** accuracy metrics (new or unlabeled samples):

```json
"predictor": {
  "blind": {
    "groups": [
      { "label": "incoming_batch_a", "sample_paths": ["configs/new_samples.csv"] }
    ]
  }
}
```

- **`groups[].label`** is metadata (batch name), not a ground-truth class.
- **Do not** set **`predictor.controls`** / **`predictor.diseases`** (or nested `controls`/`diseases` on `PredictorConfig`) in the same run as **`blind`**.
- **Output**: `prediction_report.json` has **`mode": "blind"`**, **`blind_summary`** (counts per predicted class, mean probabilities, mean entropy), and per-sample **`probabilities`**, **`predicted_subgroup`**, **`max_probability`**, **`entropy`**. **`validation_metrics.json`** is **not** written. For multiclass OvR, if a sample has **no evidence** for any head, fused probabilities are **uniform** `1/K`; use **`entropy`** and the full **`probabilities`** vector — **`predicted_subgroup`** from argmax is **not** decisive when **`max_probability` ≈ 1/K**.
- **Per-comparison projects**: a blind run produces **one** output under **`{project_root}/predictors/blind/`**. You must set **`predictor.model_path`**, **`model_dir`**, **`multiclass-classifier.pkl`** (native multiclass), or **`classifier.save_classifier_path`** (often the unified OvR bundle) so the model is unambiguous; binary per-comparison PKLs are not auto-picked for blind.

### Standalone `--config` JSON

You may either:

- Set **`controls`** and **`diseases`** nested objects (same shape as above), or  
- Pass flat **`test_control_paths`** / **`test_disease_paths`** (resolved to absolute paths), or  
- Set **`blind`** only (same `groups` / `sample_paths` shape) for unlabeled inference.

### CLI overrides (`--project` mode)

**`--test-control`** and **`--test-disease`** still override the project test set (CSV or comma-separated paths). The structured **`prediction_report.json`** then uses placeholder group labels for the CLI override.

### CLI examples

```bash
methyl-predictor --model-dir ./classifiers --output-dir ./out \
  --test-control ./test_lists/healthy.csv --test-disease ./test_lists/sick.csv
```

Holdout samples should not overlap training when you care about generalization.

### Building a multiclass OvR ECDF pickle (`ecdf_one_vs_rest`)

If you have **K** MethylDetector-style binary pickles (each with `classifier` + `dmpDF`), assemble one multiclass bundle for MethylClassifier / MethylPredictor:

```python
from pathlib import Path
from methyl_classifier.utils.ovr_bundle import (
    binary_entry_from_detector_pickle,
    save_ecdf_ovr_pickle,
)

entries = [
    binary_entry_from_detector_pickle(Path("classifier-1-A.pkl")),
    binary_entry_from_detector_pickle(Path("classifier-1-B.pkl")),
    binary_entry_from_detector_pickle(Path("classifier-1-C.pkl")),
]
save_ecdf_ovr_pickle(
    entries,
    class_names=["A", "B", "C"],
    output_path=Path("multiclass-ovr-ecdf.pkl"),
)
```

Point **`model_path`** at the saved file. Classifier order must match **`class_names`** and, for labeled evaluation, **`test_group_paths`** order (class index 0 = first group). See [IMPLEMENTATION.md](IMPLEMENTATION.md) for the fusion rule and subgroup guidance.

**MethylClassifier** can also **assemble** the same bundle at runtime from **`ovr_binary_model_paths`** / **`ovr_detection_dirs`** in the classification or project `classifier` step config (see MethylClassifier `USAGE.md`); the saved **`save_classifier_path`** output is then the portable `ecdf_one_vs_rest` dict automatically.

---

## Accuracy metrics reported (labeled runs only)

When samples have **known** expected classes (control vs disease, or multiclass `test_group_paths`), MethylPredictor computes accuracy-style metrics and writes **validation_metrics.json**. **Blind** runs skip these and only emit descriptive **`blind_summary`** statistics in **prediction_report.json**.

### Training vs holdout (generalization)

By default, labeled runs score a **single** cohort list (often the same sample paths used to build centroids). In that case **`validation_metrics.json`** includes **`evaluation_semantics": "undifferentiated"`** and a **UserWarning** is emitted: top-level **`balanced_accuracy`** describes **in-sample** fit on the scored list, not a disjoint test set.

To report **both** cohort homogeneity and **holdout generalization** in one run, set under profile **`actionConfig.predictor`**:

- **Binary:** **`holdout_controls`** and **`holdout_diseases`** (same nested shape as **`controls`** / **`diseases`**). Training paths default to the usual predictor/project cohorts; override with **`train_controls`** / **`train_diseases`** if needed. Holdout group/stage **labels** must match the training sides.
- **Multiclass:** **`holdout_group_paths`** (same shape as **`test_group_paths`**). Training paths default to the resolved evaluation groups; override with **`train_group_paths`**. The **`class_index`** set must match between train and holdout entries.

Then **`evaluation_semantics`** is **`"train_holdout"`**, with nested **`training_metrics`** and **`holdout_metrics`**, and top-level **`balanced_accuracy`** (and related scalars when present) mirror the **holdout** split when it has samples (else training). **`predictions.csv`** gains an **`evaluation_split`** column (`training` / `holdout`).

### Global metrics

| Metric | Description |
|--------|-------------|
| **accuracy** | Proportion of correct predictions over all test samples. |
| **balanced_accuracy** | Mean of per-class recall; for binary, (sensitivity + specificity) / 2. Prefer this when test set is imbalanced. |
| **confusion_matrix** | Rows = true class (0 = control, 1 = disease), columns = predicted class. |
| **n_samples** | Total number of test samples. |
| **n_classes** | Number of classes (2 for binary). |
| **class_names** | Names for each class (e.g. control, disease). |

### Per-class metrics (for each of control and disease)

| Metric | Description |
|--------|-------------|
| **precision** | For that class: predicted positives that are truly that class. |
| **recall** | For that class: true members of that class that were predicted as that class. |
| **f1** | Harmonic mean of precision and recall for that class. |
| **support** | Number of test samples in that class. |

### Binary (2-class) metrics

When the classifier is binary (control vs disease), MethylPredictor also reports:

| Metric | Description |
|--------|-------------|
| **sensitivity** | Recall for class 1 (disease): fraction of disease samples correctly predicted as disease. |
| **specificity** | Recall for class 0 (control): fraction of control samples correctly predicted as control. |
| **precision_binary** | Precision for the positive class (disease). |
| **recall_binary** | Same as sensitivity. |
| **f1_binary** | F1 for the positive class (disease). |

### Macro / weighted

| Metric | Description |
|--------|-------------|
| **macro_precision** | Mean of per-class precision. |
| **macro_recall** | Mean of per-class recall. |
| **macro_f1** | Mean of per-class F1. |
| **weighted_f1** | F1 averaged by support (weighted by class size). |

### Where to find the metrics

- **validation_metrics.json** (in `output_dir`): Full JSON-serializable dict with all metrics above, plus **`evaluation_semantics`**, and when applicable **`training_metrics`** / **`holdout_metrics`**. Use this for downstream scripts or reporting.
- **Console**: A short summary is printed after the run (for train/holdout mode, separate summaries for holdout and training when both exist).

---

## Output files

| File | Description |
|------|-------------|
| **validation_metrics.json** | Written only for **labeled** runs (accuracy, balanced_accuracy, confusion_matrix, etc.). |
| **predictions.csv** | One row per scored sample: `expected_class` when labeled; prediction, `prob_class*`, `predicted_class`. With train/holdout config, includes **`evaluation_split`**. |
| **prediction_report.json** | **`mode`**: `"labeled"` or `"blind"`. Labeled: nested **`controls` / `diseases`** (or **`multiclass_groups`**) + **`samples`** + **`validation_metrics`**. Blind: **`blind`** block + **`blind_summary`**; **`validation_metrics`** is `null`. |

All are written under `--output-dir` / `output_dir`.

---

## Related documentation

- [THEORY.md](THEORY.md) — Code-backed theoretical summary and pointer to the canonical theory chapters.
- [IMPLEMENTATION.md](IMPLEMENTATION.md) — How MethylPredictor uses MethylClassifier and computes metrics.
