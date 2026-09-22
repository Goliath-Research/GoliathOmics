# MethylClassifier Usage Guide

## Overview

MethylClassifier classifies methylation samples using trained Bayesian models. It loads classifiers produced by **MethylDetector** (per-chromosome) or single trained models and applies them to new samples. Classification can use a JSON config file or command-line arguments.

There are **two ways to run MethylClassifier**:

1. **Docker container** — Run inside the MethylPipeline container (e.g. `methylpipeline`) with dependencies provided.
2. **Local host with virtual environment** — Create a venv, install MethylUtils and MethylClassifier, activate the venv, and run on the host.

Use one or the other; the CLI and Python API are the same once the environment is active.

---

## Setup 1: Docker container

Use this when you want a reproducible environment (e.g. same as MethylCentroid/MethylDetector).

**Prerequisites:** Docker; for GPU (if used), NVIDIA Container Toolkit.

**1. Start the container**

From the MethylPipeline repo:

```bash
cd /path/to/MethylPipeline/docker
docker compose up -d
```

(Use the same container name as in your compose file; below assumes `methylpipeline` and repo mounted at `/workspace`.)

**2. Run MethylClassifier**

Paths in your config must be valid **inside** the container (e.g. `/workspace/...`). From the host:

```bash
docker exec -w /workspace/packages/methylclassifier methylpipeline \
  methyl_classifier --config /workspace/path/to/your_config.json
```

Or run the CLI module:

```bash
docker exec -w /workspace/packages/methylclassifier methylpipeline \
  python -m methyl_classifier.cli --config /workspace/path/to/config.json
```

**Common options:** `--config`, `--model`, `--model-dir`, `--input`, `--output`, `--project`, `--verbose`, etc. Paths in config must be container paths (e.g. `/workspace/...`).

---

## Setup 2: Local host with virtual environment

Use this when you run on the host (e.g. laptop or login node) and want to activate a virtual environment before running MethylClassifier.

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

**3. Install MethylUtils (required dependency)**

MethylClassifier depends on MethylUtils. Install it first:

```bash
cd /path/to/MethylPipeline/packages/methylutils
pip install -e .
```

**4. Install MethylClassifier**

```bash
cd /path/to/MethylPipeline/packages/methylclassifier
pip install -e .
# or: poetry install
```

**5. Run MethylClassifier**

With the virtual environment **activated** (`source .venv/bin/activate` from the repo root), use the CLI. Paths in the config are on the **host**; you do not use a container.

```bash
methyl_classifier --config configs/PCa_vs_Healthy_classifier_config.json
methyl_classifier --model-dir /path/to/classifiers/ --input /path/to/samples/ --output results.csv
```

---

## Quick Start

After completing either setup:

- **Docker:** Run via `docker exec -w /workspace/packages/methylclassifier methylpipeline methyl_classifier --config <config.json>` (paths in config must be valid inside the container).
- **Virtual environment:** Activate the project venv (`source .venv/bin/activate`), then run `methyl_classifier --config <config.json>` (paths in config are on the host).

---

## Command-Line Usage

### Classification with configuration file (recommended)

Use a JSON config file for reproducible runs. Example for **multi-chromosome** mode (MethylDetector output) with Platt calibration:

```bash
# From the methylclassifier package directory:
methyl_classifier --config configs/PCa_vs_Healthy_classifier_config.json

# From the MethylPipeline repo root:
methyl_classifier --config packages/methylclassifier/configs/PCa_vs_Healthy_classifier_config.json
```

Example config (`configs/PCa_vs_Healthy_classifier_config.json`):

```json
{
  "model_dir": "/work/data/david-gladys/all-prostate/detection/PCa_vs_Healthy_optimized",
  "model_path": null,
  "input_path": "/work/data/david-gladys/all-prostate",
  "output_path": "/work/data/david-gladys/all-prostate/classification/PCa_vs_Healthy.csv",
  "temperature": 1.0,
  "enable_platt_calibration": true,
  "trimmed_percentile_low": 0.10,
  "trimmed_percentile_high": 0.01,
  "chromosome_weights": null,
  "debug": false,
  "no_filter": false,
  "log_level": "INFO"
}
```

- **model_dir**: Directory containing `classifier-{chrom}.pkl` files (e.g. MethylDetector `output_dir`). Use this for multi-chromosome mode; set **model_path** to `null`.
- **input_path**: Path to a directory of sample folders (each with `{chrom}-CG.h5`, etc.) or to a single .h5 file/directory.
- **output_path**: CSV file for classification results.
- **enable_platt_calibration**: Use pre-fitted Platt calibrator from the model if available (set to `true` when MethylDetector was run with `enable_platt_calibration`).
- **trimmed_percentile_low** / **trimmed_percentile_high**: Used to compute chromosome weights from effect sizes when **chromosome_weights** is `null` and **weight_method** is not `linear_fitted`.
- **weight_method**: How to obtain chromosome weights: `config`, `effect_size`, `linear_fitted`, `logistic_fitted`, or `elasticnet_fitted`. If omitted, inferred: `config` when **chromosome_weights** is set, else `effect_size`.
- **Fitted methods** (`linear_fitted`, `logistic_fitted`, `elasticnet_fitted`) require labeled validation data (e.g. centroid validation). Weights are fitted from per-chromosome P(class1) and class labels, then projected to non-negative and sum-to-one. **linear_fitted**: linear regression (or Ridge/Lasso). **logistic_fitted**: LogisticRegression (L1/L2 or unpenalized). **elasticnet_fitted**: ElasticNet regression (L1/L2 mix via **weight_fit_l1_ratio**).
- **weight_fit_regularization**: For `linear_fitted`: `none`, `ridge`, or `lasso`. For `logistic_fitted`: `none`, `l1`, or `l2`. For `elasticnet_fitted`: ignored. Default `none`.
- **weight_fit_alpha**: Regularization strength (inverse of C for logistic). Default `1.0`.
- **weight_fit_l1_ratio**: For `elasticnet_fitted` only: balance L1/L2 (0=ridge-like, 1=lasso-like). Default `0.5`.
- **project_name**: When set, after classification the classifier is saved as `<project_name>-classifier.pkl` and the list of sample folders as `<project_name>-samples.txt` (or .csv) in the same directory as the classification output. You can override paths with **save_classifier_path** and **samples_list_export_path**.
- **`--project`**: When using a project JSON, merged config comes from `actionConfig.classifier` and project paths. **Control/disease** projects run **one binary classification job per comparison**, then—if **`ovr_binary_pickles_from_comparisons`** is **true**—automatically **export the unified multiclass OvR PKL** to **`{project_root}/classifiers/<control_label>/classifier_<control>_<project_name>.pkl`** (same file **`--export-ovr-pkl`** would write). MethylPredictor loads that path via **`model_path`** / resolver defaults. Detector probe files default to **`classifier-{first_chrom}-{sorted_contexts}.pkl`** (e.g. **`classifier-1-CG.pkl`** for CG-only projects); set **`ovr_unified_classifier_basename`** only if your detector used a different naming pattern.

#### Reusing one PKL for prediction (OvR and multi-chromosome)

After classification, **`save_classifier_path`** stores **one** file MethylPredictor can open with **`model_path`**: OvR mode writes the portable **`ecdf_one_vs_rest`** dict (not a raw per-chromosome folder). Multi-chromosome binary mode pickles the full **`MethylClassifier`**, which already aggregates all chromosomes — you are **not** meant to re-merge detector pickles on every prediction. Prefer that PKL over pointing predictors at **`model_dir`** = detector output when a saved artifact exists. See **MethylPredictor** `USAGE.md` (“Saved classifier vs raw detector directory”).

#### Export OvR PKL only (no per-comparison classification)

Use this when detectors exist but you skip the per-comparison training loop. A normal **`--project`** run with **`ovr_binary_pickles_from_comparisons`** already performs this export at the end; **`--export-ovr-pkl`** is for export-only workflows:

```bash
methyl_classifier --project path/to/project.json --export-ovr-pkl
methyl_classifier --config ovr_only.json --export-ovr-pkl /custom/out.pkl
```

- **Optional path**: If omitted, uses **save_classifier_path** from the resolved config, then **`<cwd>/<project_name>-classifier.pkl`** if **project_name** is set.
- **Requires** **ovr_binary_model_paths** or **ovr_detection_dirs** (K≥2) in the config / `actionConfig.classifier`.
- **Control/disease projects** are supported when **`actionConfig.classifier`** (or **`--step-override`**) defines **project-wide** `ovr_binary_model_paths` or `ovr_detection_dirs`, or sets **`ovr_binary_pickles_from_comparisons`: true**. That resolver fills **`ovr_detection_dirs`** with **one directory per disease comparison** and sets **`ovr_pairwise_aggregate_control`: true** unless an explicit control artifact exists (`ovr_control_vs_rest_pkl` or `detections/one_vs_rest/...`). With aggregation, you get **K** class probabilities (control + each subgroup) from **K−1** pairwise detectors plus a **synthetic control head** (geometric mean of each pairwise’s P(control)); each pairwise folder is still merged across chromosomes via **all** `classifier*.pkl` there.  
  The **exported** multiclass OvR PKL defaults to **`{project_root}/classifiers/<control_group_label>/classifier_<control>_<project_name>.pkl`**. **MethylPredictor** should use that single saved PKL (`model_path`) so prediction stays one load for the full union of DMPs across chromosomes and classes.
  **Fusion:** aggregated-control bundles record **`metadata.ovr_fuse_mode`**: **`pairwise_max_contrast`** by default (see `fuse_ovr_binary_probas` in `multiclass_ovr.py`). Use **`flat`** in the PKL metadata only if you need the legacy softmax over all K heads. Packages also persist **`metadata.ovr_inference_version`** so multiclass semantics are auditable across releases.

### Hierarchical panel readout (OvR, pairwise max-contrast)

On top of the same fused OvR logits, you can define **disease families** (e.g. prostate stages vs colorectal) and label samples as **healthy**, **primary_disease**, **alternative_panel_disease**, or **indeterminate**. This requires **`pairwise_max_contrast`** fusion (not **`flat`**).

Configure it in either place:

- **Standalone classifier JSON** (`ClassificationConfig`): top-level **`panel`** object (same keys as below).
- **Pipeline project**: **`actionConfig.classifier.panel`** — merged into the resolved classifier config the same way as other classifier step keys.

Shape (example):

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

**MethylPredictor** can use the same object under **`actionConfig.predictor.panel`** for prediction-only runs (see MethylPredictor `USAGE.md`). Implementation: **`methyl_classifier.core.panel_fusion`**; classification with **`samples_list`** or centroid-derived sample lists writes extra CSV columns and **`panel_report.json`** next to the results CSV when **`output_path`** is set.

### Low DMP coverage abstention

- **`min_observed_dmp_fraction`** (0–1, default **0**): If the fraction of panel DMPs with **observed** (finite) methylation for a sample is **below** this value, the pipeline **abstains**: uniform class probabilities, **`prediction` = -1**, **`predicted_class` = abstain**, and CSV column **`abstained` = true**. Missing DMP positions (no call or NaN at a matched locus) do not count as observed. CLI override: **`--min-observed-dmp-fraction F`**.

### Multiclass OvR (K≥2) without a separate bundle script

If you have **K** MethylDetector pickles (one per one-vs-rest class), list them in the config (or under `actionConfig.classifier` in a project JSON) instead of `model_dir` / `model_path`:

- **`ovr_binary_model_paths`**: array of K absolute paths to detector `*.pkl` files (each must contain `classifier` + `dmpDF`).
- **`ovr_detection_dirs`**: K directories (OvR order). **One** `classifier*.pkl` → single-chrom ECDF head; **several** `classifier-*.pkl` → weighted multi-chromosome expert for that class (same as loading that folder as **`model_dir`**).
- **`ovr_class_names`** or **`multiclass_class_names`** (multiclass centroid validation): length **K**, same order as the OvR sources and **`centroid_dirs`**.

MethylClassifier **assembles** the `ecdf_one_vs_rest` bundle in memory. On **save** (`save_classifier_path` / `project_name`), it writes the **portable dict** PKL used by MethylPredictor, not a raw pickled `MethylClassifier`.

Override paths from the command line if needed:

```bash
methyl_classifier --config configs/PCa_vs_Healthy_classifier_config.json \
  --model-dir /path/to/detection/output \
  --input /path/to/samples \
  --output /path/to/classification/results.csv
```

### Single-chromosome classification

Classify using one trained model (single chromosome/context):

```bash
methyl_classifier --model classifier-1-CG.pkl \
                  --input samples/ \
                  --output results.csv
```

### Multi-chromosome classification (CLI only)

```bash
methyl_classifier --model-dir /path/to/classifiers/ \
                  --input /path/to/samples/ \
                  --output results.csv
```

The directory must contain files matching `classifier-{chrom}.pkl` (e.g. `classifier-1.pkl`, `classifier-2.pkl`). Probabilities from each chromosome are combined using weights from **weight_method**: trimmed-mean effect_size (default), predefined **chromosome_weights** (`config`), or **linear_fitted** (fit from validation data when centroid validation is used).

### Alternative: samples list in config

Instead of **input_path**, you can pass a list of sample directories in the config (**samples**). Each directory should contain `{chrom}-CG.h5` (and optionally CHG/CHH) per chromosome. See `CONFIG_FILE_GUIDE.md` for the full schema.

---

## Using one or several contexts

Contexts (CG, CHG, CHH) are **set in MethylDetector**, not in MethylClassifier. The detector trains per-chromosome classifiers using DMPs from the contexts you specify; the saved model metadata stores those contexts. MethylClassifier reads **model_contexts** from the loaded model and loads only the corresponding sample files for each chromosome.

### Single context (e.g. CG only)

- In **MethylDetector** config, set `"contexts": ["CG"]`.
- Sample directories must contain `{chrom}-CG.h5` for each chromosome. MethylClassifier will load only CG; CHG/CHH are skipped. This is faster and often sufficient for many applications.

### Multiple contexts (CG + CHG + CHH)

- In **MethylDetector** config, set `"contexts": ["CG", "CHG", "CHH"]`.
- Sample directories must contain `{chrom}-CG.h5`, `{chrom}-CHG.h5`, and `{chrom}-CHH.h5` for each chromosome. MethylClassifier merges contexts (union of positions) so that more DMPs contribute; this can improve accuracy at the cost of more data and compute.

**Important:** The model and samples must match. If the model was trained with multiple contexts, provide all context files; if it was trained with CG only, provide only `{chrom}-CG.h5`. MethylClassifier infers which contexts to load from the model metadata and will log e.g. "Model is CG-only: loading only CG context from samples".

**Practical tip:** Start with CG-only for speed and simplicity; add CHG/CHH in MethylDetector if validation balanced accuracy is insufficient or for context-specific biology.

---

## Improving the classifier's balanced accuracy

Balanced accuracy (average of per-class accuracy) can be improved both **upstream** (MethylDetector) and in **MethylClassifier** settings.

### Upstream (MethylDetector)

- **More or better DMPs:** Relax the significance or biological filters so more DMPs are retained (e.g. increase `alpha`, lower `min_delta_mean`, raise `max_bc`). Alternatively, use **target_balanced_accuracy** so the detector selects enough DMPs to meet the target on validation.
- **Effect size:** DMPs are ranked by effect_size, and per-DMP weights in the classifier are derived from effect_size. Detector biological filters and DMP selection therefore directly affect classifier accuracy. See [MethylDetector CLASSIFIER_WEIGHTS_AND_ACCURACY](../methyldetector/docs/CLASSIFIER_WEIGHTS_AND_ACCURACY.md) for how effect_size flows into the classifier.
- **Contexts:** Adding CHG/CHH (if not already) in MethylDetector can add discriminative DMPs and improve accuracy.

### MethylClassifier side

- **Chromosome weights:** Use **weight_method** to combine per-chromosome probabilities. Default **effect_size** uses a trimmed mean of effect_size per chromosome. Use **config** with predefined **chromosome_weights** if you know which chromosomes are most informative. Use **linear_fitted**, **logistic_fitted**, or **elasticnet_fitted** when you have validation labels (e.g. centroid validation): weights are fitted from per-chromosome P(class 1) and labels, which can improve balanced accuracy when some chromosomes are more discriminative.
- **Platt calibration:** Set **enable_platt_calibration: true** if the model was trained with calibration; this improves probability estimates and can help threshold-based decisions.
- **Temperature:** Adjust **temperature** (e.g. &lt; 1 to sharpen, &gt; 1 to soften) for confidence calibration.
- **Validation:** Use centroid validation (**centroid1_dir** / **centroid2_dir** or **centroid1_sample_paths** / **centroid2_sample_paths**) so the CLI reports balanced accuracy on centroid samples; use that to tune detector and classifier settings.

### Summary of levers

| Lever | Where | Effect on balanced accuracy |
|-------|--------|-----------------------------|
| alpha, min_delta_mean, max_bc, target_balanced_accuracy | MethylDetector | More/better DMPs and effect_size ranking improve classifier accuracy. |
| contexts | MethylDetector | Adding CHG/CHH can add discriminative DMPs. |
| weight_method, chromosome_weights | MethylClassifier | Better chromosome weighting can improve combined prediction. |
| enable_platt_calibration, temperature | MethylClassifier | Better-calibrated probabilities and decision boundaries. |

---

## Python API

### Basic Classification (Single Chromosome)

```python
from methyl_classifier import MethylClassifier, ClassifierConfig
from methyl_utils import MethylSample

# Load single classifier
config = ClassifierConfig(
    model_path="classifier-1-CG.pkl",
    temperature=1.0
)
classifier = MethylClassifier(config)

# Load and classify sample
sample = MethylSample.load_from_h5("patient_001.h5")
methylation_data = sample.methylation_levels  # Extract features

prediction = classifier.predict(methylation_data)
probabilities = classifier.predict_proba(methylation_data)

print(f"Prediction: {prediction}")
print(f"Probabilities: {probabilities}")
```

### Multi-Chromosome Classification

```python
from methyl_classifier import MethylClassifier, ClassifierConfig

# Load all chromosome classifiers from directory
config = ClassifierConfig(
    model_dir="/path/to/classifiers/",  # Contains classifier-1.pkl, classifier-2.pkl, etc.
    temperature=1.0,
    trimmed_percentile_low=0.10,   # Remove bottom 10% of effect sizes (less informative DMPs)
    trimmed_percentile_high=0.01   # Remove only top 1% of effect sizes (outliers, preserving important DMPs)
)

# Or use predefined weights (weight_method="config"):
# config = ClassifierConfig(
#     model_dir="/path/to/classifiers/",
#     chromosome_weights={'1': 0.4, '2': 0.3, '3': 0.3}
# )
# Or fit weights from validation data (weight_method="linear_fitted"):
# config = ClassifierConfig(model_dir="/path/to/classifiers/", weight_method="linear_fitted")
# Then call classifier.fit_chromosome_weights(chrom_proba_matrix, labels, method="linear", regularization="ridge", alpha=1.0)
# with (n_samples x n_chroms) chrom_proba_matrix and binary labels.

classifier = MethylClassifier(config)

# Classify sample (will combine weighted probabilities from all chromosomes)
prediction = classifier.predict(methylation_data)
probabilities = classifier.predict_proba(methylation_data)

print(f"Multi-chromosome prediction: {prediction}")
print(f"Weighted probabilities: {probabilities}")
print(f"Chromosome weights: {classifier.chromosome_weights}")
```

### Classifying multiple samples with merged contexts

```python
from methyl_classifier import MethylClassifier, ClassifierConfig
from methyl_classifier.cli import classify_samples

config = ClassifierConfig(
    model_dir="/path/to/classifiers/",
    trimmed_percentile_low=0.10,
    trimmed_percentile_high=0.01,
    enable_platt_calibration=True,
)
classifier = MethylClassifier(config)

sample_dirs = [
    "/path/to/sample1/",  # Contains 1-CG.h5, 1-CHG.h5, 1-CHH.h5, etc.
    "/path/to/sample2/",
    "/path/to/sample3/"
]

classify_samples(
    classifier=classifier,
    samples_list=sample_dirs,
    output_file="results.csv",
    debug=False
)
```

Or use a config file with **samples** instead of **input_path**:
```json
{
  "model_dir": "/path/to/classifiers/",
  "model_path": null,
  "samples": ["/path/to/sample1/", "/path/to/sample2/"],
  "output_path": "results.csv",
  "temperature": 1.0,
  "enable_platt_calibration": true,
  "trimmed_percentile_low": 0.10,
  "trimmed_percentile_high": 0.01,
  "chromosome_weights": null,
  "debug": false,
  "no_filter": false,
  "log_level": "INFO"
}
```

### Classification with P-Values

Test if samples belong to centroids:

```python
from methyl_utils import MethylSample

# Load centroids and test sample
centroid_healthy = MethylSample.load_from_h5("healthy_centroid.h5")
centroid_cancer = MethylSample.load_from_h5("cancer_centroid.h5")
test_sample = MethylSample.load_from_h5("patient_001.h5")

# Test goodness of fit
p_healthy = centroid_healthy.prob_belongs(test_sample)
p_cancer = centroid_cancer.prob_belongs(test_sample)

print(f"P(belongs to healthy): {p_healthy:.4f}")
print(f"P(belongs to cancer): {p_cancer:.4f}")

# Interpret results
if p_healthy > 0.05 and p_cancer < 0.05:
    print("Classification: Healthy (high confidence)")
elif p_cancer > 0.05 and p_healthy < 0.05:
    print("Classification: Cancer (high confidence)")
elif p_healthy > 0.05 and p_cancer > 0.05:
    print("Ambiguous: Fits both centroids")
else:
    print("Outlier: Doesn't fit either centroid")
```

## Configuration parameters (config JSON)

When using `--config`, the JSON file can include:

### Model
| Field | Type | Description |
|-------|------|-------------|
| `model_dir` | string or null | Directory with `classifier-{chrom}.pkl` (multi-chromosome; e.g. MethylDetector output_dir) |
| `model_path` | string or null | Single classifier .pkl (single-chromosome). Use `null` when using `model_dir` |
| `temperature` | number | Softmax temperature (default: 1.0) |
| `enable_platt_calibration` | boolean | Use pre-fitted Platt calibrator from model if present (default: false) |
| `class_priors` | array or null | Optional `[p_class0, p_class1]` prior weights for binary ECDF posterior (defaults to uniform) |
| `dependence_block_size` | integer | Optional block size for dependence-aware aggregation (`1` = independent-loci baseline) |
| `dependence_block_shrinkage` | number | Optional shrinkage in `[0,1]` for block-level aggregation (`0` = no shrinkage) |
| `ovr_fuse_mode` | string or null | Optional OvR fusion override: `flat` or `pairwise_max_contrast` (else use model metadata) |
| `trimmed_percentile_low` | number | Lower percentile for chromosome weight from effect_size (default: 0.10) |
| `trimmed_percentile_high` | number | Upper percentile for chromosome weight (default: 0.01) |
| `chromosome_weights` | object or null | Optional `{"1": 0.4, "2": 0.3, ...}`; if set, overrides trimmed-mean weights |

### Input / output
| Field | Type | Description |
|-------|------|-------------|
| `input_path` | string | Path to .h5 file or directory of sample folders (required unless `samples` is set) |
| `samples` | array of strings | Alternative: list of sample directory paths (each with `{chrom}-CG.h5`, etc.) |
| `output_path` | string or null | Output CSV path for classification results |

### Run options
| Field | Type | Description |
|-------|------|-------------|
| `debug` | boolean | Extra debug output (default: false) |
| `no_filter` | boolean | Process all .h5 without chromosome/context filter (default: false) |
| `log_level` | string | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` (default: `"INFO"`) |

## Output Format

### CSV Output

```csv
sample_id,prediction,probability_class0,probability_class1,confidence,classification
patient_001,1,0.15,0.85,high,Cancer
patient_002,0,0.92,0.08,high,Healthy
patient_003,1,0.45,0.55,low,Cancer
```

### JSON Output

```json
{
  "patient_001": {
    "prediction": 1,
    "probabilities": [0.15, 0.85],
    "class_names": ["Healthy", "Cancer"],
    "confidence": "high",
    "classification": "Cancer"
  }
}
```

## Model Information

Trained models contain:
- `classifier`: ECDF-based classifier instance
- `metadata`: Training information (chromosome, context, DMPs, accuracy)
- `selected_dmps_df`: DataFrame of selected DMPs

To inspect a model:

```python
import pickle

with open("classifier_chr1-CG.pkl", "rb") as f:
    model_package = pickle.load(f)
    
print(f"Chromosome: {model_package['metadata']['chromosome']}")
print(f"Context: {model_package['metadata']['context']}")
print(f"Number of DMPs: {model_package['metadata']['n_dmps']}")
print(f"Validation accuracy: {model_package['metadata']['validation']['overall_accuracy']:.2%}")
```

## Quality Control

Use `prob_belongs()` for quality control:

```python
from methyl_utils import MethylSample

# Load reference centroids
centroid_ref = MethylSample.load_from_h5("reference_centroid.h5")

# Check if samples belong
samples = ["sample_001.h5", "sample_002.h5", "sample_003.h5"]
outliers = []

for sample_path in samples:
    sample = MethylSample.load_from_h5(sample_path)
    p_value = centroid_ref.prob_belongs(sample)
    
    if p_value < 0.01:  # Strict threshold
        outliers.append((sample_path, p_value))
        print(f"⚠️  {sample_path}: Outlier detected (p={p_value:.6f})")
    else:
        print(f"✓  {sample_path}: Normal (p={p_value:.4f})")

print(f"\nFound {len(outliers)} outliers out of {len(samples)} samples")
```

## Troubleshooting

### Model Loading Errors
- Verify model file exists and is readable
- Check pickle compatibility (models trained with older versions may have issues)
- Use CustomUnpickler for backward compatibility

### Low Confidence Classifications
- Check sample quality (coverage, methylation levels)
- Use `prob_belongs()` to verify sample belongs to known groups
- Consider retraining model with more DMPs

### Memory Issues
- Process samples in batches
- Use GPU if available (`use_gpu=True`)
- Reduce number of concurrent samples

## Integration

### Aggregating MethylDetector outputs (multi-chromosome)

After **MethylDetector** has been run for all chromosomes, it writes one classifier per chromosome in its output directory (e.g. `classifier-1.pkl`, `classifier-2.pkl`, ...). **MethylClassifier** can load that directory and aggregate all chromosome classifiers:

1. Set **MethylDetector** `output_dir` (e.g. `/path/to/PCa_vs_Healthy_optimized`) so it contains `classifier-{chrom}.pkl` for each chromosome.
2. Point **MethylClassifier** at that directory with **multi-chromosome mode**:
   - Config: `"model_dir": "/path/to/PCa_vs_Healthy_optimized"` (and `"model_path": null`).
   - CLI: `methyl-classifier --model-dir /path/to/PCa_vs_Healthy_optimized --input /path/to/samples/ --output results.csv`
3. MethylClassifier will load all `classifier-*.pkl` files, compute chromosome weights from the saved DMP weights (trimmed-mean), and combine predictions with a weighted average of per-chromosome probabilities.

Sample directories for classification should contain `{chrom}-CG.h5` (and optionally `{chrom}-CHG.h5`, `{chrom}-CHH.h5`) per chromosome, as expected by the data loader.

MethylClassifier is part of the MethylPipeline ecosystem:
- Uses models from **MethylDetector** (per-chromosome classifiers) or single trained models
- Classifies samples against **MethylCentroid** references
- Integrates with **MethylUtils** for p-value testing

