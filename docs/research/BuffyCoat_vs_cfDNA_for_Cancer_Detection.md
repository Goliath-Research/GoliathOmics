# Buffy coat vs cfDNA methylation (MethylPipeline analyte choice)

**Status:** research / design note (not operator runbook).  
**Canonical config:** `[docs/ANALYTE_PROFILES.md](../ANALYTE_PROFILES.md)` — set `regulatory.primary_analyte` to `buffy_coat`, `cfdna`, or `combined`.  
**Empirical comparison canvas:** [analyte-comparison](../canvas/README.md#analyte-comparison).  
**Landscape context:** `[Gemini_on_Cancer_Detection.md](Gemini_on_Cancer_Detection.md)` (MCED competitors / analytes) and `[Grok_on_Gemini_conclusions.md](Grok_on_Gemini_conclusions.md)` (verification + caveats).

MethylPipeline is **analyte-agnostic**: the same DomainProgram + profile path runs for leukocyte DNA and plasma cfDNA. Analyte choice changes biology, QC defaults, and how you interpret features — not which CLI you use.

**Ω set-point clustering (research):** leukocyte proportions may form healthy composition clusters; see [`omega-cluster-detection.md`](omega-cluster-detection.md) for a leakage-safe matched-stratum analysis on Buffy PCa (pipeline leave expansion deferred).

---



## Biological roles (not competitors by default)


| Analyte                        | What the methylation signal mainly reflects                                                                                                  | Typical detection role                                                           |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| **cfDNA (plasma)**             | DNA shed into circulation, including tumor-derived fragments when tumor fraction is high enough; tissue-of-origin and fragmentomic structure | Direct tumor / MCED / monitoring / MRD-oriented signal                           |
| **Buffy coat (leukocyte DNA)** | Host immune / systemic epigenome (inflammation, aging, exposures, cell-type mix); also matched hematopoietic background for plasma assays    | Indirect risk / host-response; CHIP / germline noise filter; complementary layer |


cfDNA methylation assays can carry **tumor-derived** patterns and support tissue-of-origin style inference when tumor fraction and assay design allow it ([Liu et al., PNAS 2023](https://www.pnas.org/doi/10.1073/pnas.2209852119); MCED methylation reviews such as [Chen et al., Cancer Cell 2022](https://www.sciencedirect.com/science/article/pii/S153561082200513X)). Buffy-coat methylation is usually **not tumor DNA**; it tracks host state that may correlate with cancer risk or presence ([WBC methylation epidemiology overview](https://pmc.ncbi.nlm.nih.gov/articles/PMC6896050/)).

**Implication for this pipeline:** treat the two as complementary analytes under one orchestration model — matching how the broader liquid-biopsy field pairs plasma signal with leukocyte background rather than forcing a single-analyte bet.

---



## What the MCED landscape implies (Gemini + Grok)

The commercial MCED / liquid-biopsy field has moved past “cfDNA methylation alone” (GRAIL Galleri-style) toward **multiomics** and **noise control**. Summarized from `[Gemini_on_Cancer_Detection.md](Gemini_on_Cancer_Detection.md)`; accuracy and caveats in `[Grok_on_Gemini_conclusions.md](Grok_on_Gemini_conclusions.md)`:


| Industry pattern                         | Examples (landscape notes)                                                                                                     | Relevance to MethylPipeline                                                                                                                                                                |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **cfDNA methylation ± fragmentomics**    | Gene Solutions SPOT-MAS; Delfi (fragmentomics-first); Freenome multiomic stack                                                 | Pipeline already supports methylation + **cfDNA fragmentomics** QC/features when `primary_analyte: cfdna`                                                                                  |
| **Multiomics beyond DNA methylation**    | Exact Cancerguard (methylation + mutations + proteins); Freenome (+ cfRNA, proteins, immune)                                   | Out of scope as first-class analytes today; do not invent protein/RNA steps in Python — keep study design honest about methylation±fragmentomics coverage                                  |
| **Buffy coat as CHIP / germline filter** | Guardant (plasma + buffy to subtract hematopoietic mutations); Freenome / Exact use WBC DNA for background noise in validation | Critical for **mutation** assays. For **methylation** studies, paired buffy still helps: matched host epigenome, cell-type confounding, and “is this plasma signal leukocyte-like?” checks |
| **Bisulfite vs enrichment chemistry**    | Adela cfMeDIP (affinity, bisulfite-free) vs chemical conversion                                                                | MethylPipeline assumes **bisulfite WGBS / extractor** inputs from Illumina sequencing; chemistry choice is upstream of this repo                                                           |


Grok’s caveats apply here too: published sensitivity/specificity are stage- and cohort-dependent; commercial status differs by product (e.g. Guardant Shield is CRC-focused in its approved form); do not copy vendor AUCs into pipeline success metrics.

**CHIP in one paragraph:** aging hematopoietic clones shed mutated DNA into plasma. Mutation-only cfDNA tests can call CHIP as solid-tumor signal. Sequencing buffy coat alongside plasma lets pipelines subtract leukocyte-origin variants. Methylation-first assays are less about CHIP SNVs and more about **host leukocyte methylomes contaminating or confounding plasma features** — still a reason to keep buffy as a first-class analyte and to prefer paired designs when logistics allow.

---



## Goal → preferred analyte (within MethylPipeline)


| Goal                                                                   | Prefer                                | Why in this stack                                                                                                                                           |
| ---------------------------------------------------------------------- | ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Early detection / MCED-like tumor signal, localization, response / MRD | `cfdna`                               | Tumor-shed DNA + fragmentomics (`methyl-fragmentomics`) and cfDNA enricher defaults — aligned with methylation+fragmentomics industry direction             |
| Host-risk / systemic signatures, abundant DNA, simpler preanalytics    | `buffy_coat`                          | Stable leukocyte DNA; alignment/bisulfite guardrails without cfDNA fragmentomics profile                                                                    |
| CHIP / germline / leukocyte-background control for plasma work         | **Paired** `cfdna` **+** `buffy_coat` | Same subjects: plasma for tumor-oriented signal, buffy for host/hematopoietic background (industry pattern; methylation analogue of Guardant-style pairing) |
| Host score + tumor burden / ToO                                        | `combined` or **paired projects**     | Same workflow engine; separate manifests or matched cohort IDs                                                                                              |
| Assay development / feature stability under MC                         | Either, then compare                  | MC gene recurrence + cross-analyte concordance (analyte-comparison canvas)                                                                                  |


Published MCED performance claims are **assay- and cohort-specific**. Use MethylPipeline MC stability, hold-out, and analyte-match guards — not vendor brochure numbers.

---



## How MethylPipeline encodes the choice

Set once in the study manifest:

```json
"regulatory": {
  "primary_analyte": "cfdna"
}
```

Allowed tokens normalize to `cfdna`, `buffy_coat`, or `combined` (`packages/methylutils/methyl_utils/analyte_profiles.py`). With `auto_apply_analyte_profile` left on, the resolver merges analyte defaults into `actionConfig` (profile/site keys still win).


| Layer                | `cfdna`                                          | `buffy_coat`                                            |
| -------------------- | ------------------------------------------------ | ------------------------------------------------------- |
| Alignment QC         | Guardrails + bisulfite + **cfDNA fragmentomics** | Guardrails + bisulfite (no cfDNA fragmentomics profile) |
| Fragmentomics action | Enabled (`profile: cfdna`)                       | Disabled                                                |
| Enricher defaults    | `cancer-core` + broader CIS-BP modes             | CIS-BP gene_sets emphasis                               |
| Validation           | `enforce_training_analyte_match: true`           | Typically `false`                                       |


Sample prep is shared: SamplePrepPipeline → alignment QC → (optional fragmentomics) → extract → extraction QC. See `[docs/implementation/sample-preparation-flow.md](../implementation/sample-preparation-flow.md)`.

Orchestration stays DomainProgram-first (`methyl-workflow-run`); analyte does not select a legacy CLI.

---



## Depth: is ~30× enough?

Clarify what “depth” means here:

- Libraries are typically sequenced on **Illumina instruments** (e.g. NovaSeq). That is the sequencing platform — not the Infinium MethylationEPIC **BeadChip array**. MethylPipeline’s production path is **WGBS / extractor-based** methylation from BAM/FASTQ, so depth means **genome-wide sequencing coverage**, not array probe intensity.
- For **buffy-coat / leukocyte WGBS**, ~30× whole-genome–equivalent depth on Illumina is generally **technically adequate** for CpG-level methylation once reads are aggregated. Further depth usually yields diminishing returns; variance is dominated by biology (cell-type composition, inter-individual differences), not Poisson sampling of methylated counts ([related WBC methylation depth discussion](https://pmc.ncbi.nlm.nih.gov/articles/PMC10262593/)).
- For **cfDNA**, depth and library complexity matter more because **tumor fraction** can be very low in early disease. “Enough depth” is inseparable from tumor fraction, duplex/UMI design, and feature aggregation — not a single magic number copied from buffy coat.

**Practical rule for this repo**

- Buffy-only classifier: invest in **feature selection, cell-type awareness, and MC stability**, not automatically in deeper sequencing past a solid WGBS depth.
- cfDNA / MCED-oriented work: invest in **preanalytics, fragmentomics QC, tumor-fraction–aware design**, and analyte-matched training (`model_training_analyte` / plasma retrain path).
- Depth does **not** convert buffy coat into tumor DNA.

---



## Recommended study designs on this pipeline

1. **Buffy-first development** — `primary_analyte: buffy_coat`, MC stability + freeze on leukocyte cohorts; use for host markers and operational learning with abundant DNA.
2. **Plasma / cfDNA production path** — `primary_analyte: cfdna`, fragmentomics on, enforce training analyte match; see `[packages/methylvalidation/docs/PLASMA_RETRAIN_PATH.md](../../packages/methylvalidation/docs/PLASMA_RETRAIN_PATH.md)`. Aligns with methylation + fragmentomics MCED direction (SPOT-MAS / Freenome-style DNA layers — not full protein/RNA multiomics).
3. **Paired plasma + buffy (preferred when both tubes exist)** — separate study manifests (or `combined`) with matched subject IDs: cfDNA for tumor-oriented signal; buffy for host epigenome and leukocyte-background control (methylation analogue of industry CHIP/germline filtering). Compare with the analyte-comparison rubric (MC gene recurrence, cross-analyte concordance, discovery direction agreement).
4. **Do not** assume a buffy-trained panel transfers to plasma without retrain and analyte-match validation.
5. **Do not** treat MethylPipeline as a full Cancerguard/Freenome multiomic stack — proteins, cfRNA, and mutation CHIP subtraction are outside the current DomainProgram surface; document study claims accordingly.

Empirical prostate buffy vs plasma MC comparison (marginal separation on one rubric; shared gene core) lives in the canvas and on-disk under `/work/projects/prostate-cancer/analyte_comparison/` when present — use it as **assay-development evidence**, not a clinical performance claim.

---



## Bottom line

- **cfDNA** is the better *direct* analyte for tumor detection and localization when logistics allow; industry peers reinforce methylation **plus** fragmentomics (and often more modalities).
- **Buffy coat** is not only a host-risk analyte: in the MCED field it is also the standard **hematopoietic / germline background** control. MethylPipeline should keep it first-class for host signatures *and* paired plasma designs.
- The pipeline already accepts both; choose via `regulatory.primary_analyte` and profiles, validate with MC + hold-out under the matching analyte, and prefer paired cohorts when both materials are available — not deeper buffy sequencing alone.

---



## Related

- `[docs/ANALYTE_PROFILES.md](../ANALYTE_PROFILES.md)` — analyte defaults and study examples
- `[docs/implementation/sample-preparation-flow.md](../implementation/sample-preparation-flow.md)` — shared prep + cfDNA fragmentomics QC
- [analyte-comparison canvas](../canvas/README.md#analyte-comparison) — buffy vs plasma MC comparison explorer
- `[Gemini_on_Cancer_Detection.md](Gemini_on_Cancer_Detection.md)` — MCED competitors, multiomics, buffy as CHIP filter
- `[Grok_on_Gemini_conclusions.md](Grok_on_Gemini_conclusions.md)` — verification of that landscape note and performance/regulatory caveats

---

It is biologically possible to find prostate cancer signals in the buffy coat (leukocytes), but **not in the form of physical prostate tumor cells or direct prostate tumor DNA.**

Instead, the buffy coat acts as an epigenetic mirror reflecting a systemic host response to the cancer.

The biology of how cancer presents in the buffy coat is distinct from how it appears in tissue or cfDNA, operating through entirely different diagnostic mechanisms:

---



## 1. The Biological Mechanism: The Systemic Immune Response

The buffy coat contains peripheral blood mononuclear cells (PBMCs) and granulocytes. These immune cells are constantly interacting with the tumor microenvironment or responding to circulating inflammatory signals shed by a growing prostate tumor.

- **Immune Cell Reprogramming:** When an aggressive prostate tumor starts proliferating (especially transitioning to Pattern 4), it releases systemic cytokines and chemokines. The bone marrow and circulating leukocytes alter their transcription profile to adapt, which physically alters the **leukocyte DNA methylation patterns**.
- **The Field Effect:** In epidemiology, this is referred to as a "constitutional" or "soma-wide" epigenetic reflection. The leukocytes act as a proxy sensor—they change their epigenetic landscape in response to the malignancy elsewhere in the body.



## 2. Direct Tissue Markers vs. Buffy Coat Markers

Direct prostate cancer tissue genes do not transfer their methylation status to the buffy coat.

A prominent study evaluating *GADD45a* methylation in prostate cancer demonstrated this boundary explicitly:

> While *GADD45a* hypermethylation was found at extremely high levels in the serum (cfDNA) of malignant prostate cancer patients, **there was no significant difference in buffy coat methylation between cancer and benign patients**.

Therefore, you cannot look for *GSTP1*, *APC*, or *GADD45a* hypermethylation in the buffy coat to diagnose prostate cancer. If you find *GSTP1* methylation in the buffy coat, it is likely a false positive caused by age-related mutations in the blood itself (Clonal Hematopoiesis of Indeterminate Potential, or CHIP).

## 3. What the Buffy Coat *Can* Detect: Aggressiveness Gradients

Instead of looking for tissue-specific anchors, Epigenome-Wide Association Studies (EWAS) looking at leukocyte DNA have found completely unique sets of differentially methylated regions (DMRs) that correlate with high Gleason scores:

- **Transcription Factor Alterations:** An EWAS profiling leukocyte DNA from prostate cancer patients identified **77 differentially methylated regions/genes (DMRs)** in the blood cells that directly trended upwards with increasing Gleason scores. These were heavily enriched for homeobox genes (*HOXD8*) and zinc finger proteins (*ZNF-471*) controlling immune-system transcription pathways.
- **Global Hypomethylation:** Some research points to a general, systemic loss of global methylation (measured via LINE-1 repetitive elements) in leukocytes as an indicator of genomic instability or high cancer risk, though its ability to finely distinguish a 3+3 from a 3+4 remains a point of active study.

---



## References for Your Team

For your bioinformaticians and assay designers, these peer-reviewed papers map out exactly what is—and isn't—possible in the buffy coat:

- **On the divergence of serum (cfDNA) vs. Buffy Coat markers:**
- *Reis, I. M., Ramachandran, K., Speer, C., Gordian, E., & Singal, R.* **Serum GADD45a methylation is a useful biomarker to distinguish benign vs malignant prostate disease.** *British Journal of Cancer*. This study highlights why direct tumor suppressor methylation scales beautifully in serum cfDNA but fails completely when looked for in matching patient buffy coats.
- **On using leukocyte DNA methylation arrays to predict Gleason severity:**
- *Wang, X., et al.* **Epigenome-Wide Association Study of Prostate Cancer Identifies DNA Methylation Biomarkers for Aggressive Disease.** *Biomolecules*. This paper maps out the 77 unique immune/leukocyte DMRs (like *HOXD8* and *SOX11*) that track directly with higher Gleason scores, proving that leukocyte DNA has a distinct "aggressiveness signature" separate from tissue markers.
- **On systemic heritable methylation risk profiles in blood cells:**
- *Minerva Access (University of Melbourne Archive).* **Heritable methylation marks associated with breast and prostate cancer risk.** Documentation tracking how pre-diagnostic buffy coat and PBMC samples harbor constitutional methylation marks (*VTRNA2-1* promoter region) specifically predictive of developing aggressive prostate variants.

---

Assuming that plasma-derived cfDNA is completely off the table, the diagnostic strategy must pivot entirely to treating the **buffy coat as a complex biosensor**.

Without tumor DNA shedding directly into the blood, you are left with bulk leukocyte DNA. The challenge is that a raw, uncorrected buffy coat methylome is massively confounded: the differences in methylation you see between a Gleason 3+3 and a 3+4 patient are often just reflections of changing white blood cell proportions (e.g., an elevated neutrophil-to-lymphocyte ratio driven by tumor-induced systemic inflammation).

This is precisely where the **Houseman algorithm** (and its modern iterations like *EpiDISH* or *HiTIMED*) becomes the core engine of the bioinformatics pipeline.

---

## What the Houseman Algorithm Does (The Mathematical Deconvolution)

The Houseman algorithm is a reference-based **cell-type deconvolution algorithm**. It models the bulk methylation data ($Y$) of your buffy coat sample as a linear combination of pure leukocyte cell-type methylation profiles ($M$) multiplied by their unknown proportions ($\Omega$):

$$Y = M\Omega^T$$

By running a constrained quadratic programming (QP) projection, it reconstructs the exact cellular composition (proportions of T-cells, B-cells, NK-cells, monocytes, and granulocytes) out of your mixed blood sample using only a handful of cell-lineage specific CpG sites.

In a cfDNA-free buffy coat pipeline, Houseman helps you in two distinct ways:

---

## 1. The Confounder Strategy (Correcting the Noise)

If you want to find a true, direct "epigenetic footprint" left by a Gleason 3+4 tumor on the immune system, you have to strip away the noise of shifting cell counts.

* **The Workflow:** You use Houseman to calculate the exact cellular fractions ($\Omega$) for each patient. You then input these fractions as *covariates* into your Epigenome-Wide Association Study (EWAS) regression models.
* **The Value:** This allows your machine learning models to isolate **cell-type-independent aberrant DNA methylation**. It answers the question: *“Holding the number of T-cells and monocytes perfectly constant, which specific CpG sites are being hyper-methylated by the presence of a Pattern 4 tumor?”*

## 2. The Biomarker Strategy (Treating Proportions as the Signal)

Alternatively, the shifting cell proportions calculated by Houseman can *become* the diagnostic test itself. The tumor microenvironment of an aggressive prostate cancer systemically reprograms systemic immunity.

* **The Workflow:** Instead of looking at individual CpGs, your diagnostic features become the calculated cell proportions themselves (e.g., tracking subtle drops in CD8+ T-cells or shifts in specific monocyte subsets derived via Houseman deconvolution).
* **The Value:** You are using the algorithm to perform a "virtual flow cytometry" on frozen or archived buffy coat DNA. These algorithmic proportions are fed into a random forest or neural network to predict if the systemic immune profile matches a dangerous 3+4 gradient or an indolent 3+3 baseline.

---

## MethylPipeline implementation (Ω → tabular)

Buffy-coat composition is implemented as a **separate track** from DMP/gene SaMD MC (no centroid/detector):

| Piece | Location |
|-------|----------|
| Action | `pipeline.cell_deconvolution` / CLI `methyl-cell-deconv` |
| Package | `packages/methyldeconv` |
| Reference \(M\) | Packaged FlowSorted.Blood.EPIC **IDOL** (~450 markers × CD8T, CD4T, NK, Bcell, Mono, Neu), hg38-mapped |
| Output | `{output_base}/cell_fractions/cell_fractions.csv` — always all six Ω per sample |
| Modeling | Profile `cell_deconv` (or lifecycle covariates_path): tabular backend; Ω columns + clinical (sex/age/BMI) via `covariates_path` |
| Program | `workflow_engine/domain/fixtures/cell_deconv_tabular.program.json`; also nodes on study/samd lifecycle programs |
| Plan | [`docs/plans/buffy-cell-deconvolution.plan.md`](../plans/buffy-cell-deconvolution.plan.md) |

This is the **biomarker (proportions-as-signal)** path. Cell-type–adjusted DMP discovery (confounder residualization) is not in this action.

---

## Key References for Your Pipeline

To implement or adapt this algorithm into your current pipeline, your software team should reference these foundational publications:

* **The Original Houseman Methodology Paper:**
* *Houseman, E. A., et al.* **DNA methylation arrays as surrogate measures of cell mixture distribution.** *BMC Bioinformatics*. This is the core reference paper detailing the mathematical framework of using quadratic programming to project mixed whole-blood/buffy coat samples onto purified cell lines.


* **Comparative Assessment & Package Implementations (EpiDISH):**
* *Teschendorff, A. E., et al.* **A comparison of reference-based algorithms for correcting cell-type heterogeneity in Epigenome-Wide Association Studies.** *BMC Bioinformatics*. This study compares Houseman to Robust Partial Correlation (RPC) and outlines the `EpiDISH` R/Python package library, which updates the Houseman algorithm for faster compute speeds.


* **Application directly to Prostate Cancer Deconvolution:**
* *HiTIMED Framework:* **Tumor microenvironment deconvolution identifies cell-type-independent aberrant DNA methylation and gene expression in prostate cancer.** *PMC/ResearchGate (2023/2024 archive)*. This research explicitly demonstrates using advanced reference-based deconvolution matrices on prostate patient tissues and buffy coats to pull out clean, disease-specific signatures past cellular confounding.

---

Since the initial rollout of **HiTIMED** (Hierarchical Tumor Immune Microenvironment Deconvolution) around 2023, the field of epigenetic deconvolution has expanded past R-centric packages (like `minfi`, `EpiDISH`, and `FlowSorted.Blood.EPIC`) toward unified pythonic pipelines, native matrix optimizations, and cross-modal models.

If you want a modern alternative that bypasses R entirely or implements reference-based / machine-learning deconvolution frameworks natively in Python, the best path forward depends on your architectural goals:

---

## 1. Native Python Matrix Alternatives (For Custom Arrays)

If you already have your data structured into `{chr}-{ctx}.h5` HDF5 files or target tables, you don't necessarily need a monolithic packaging wrapper. The Houseman algorithm itself is fundamentally **Constrained Quadratic Programming (QP)**.

In Python, the direct, robust equivalent to Houseman's linear combination projection is implemented using **`scipy.optimize.minimize`** (using the Sequential Least Squares Programming or 'SLSQP' method) or **`cvxpy`**.

Your team can write a lightweight pythonic deconvolution function natively matching Houseman's operational matrix constraints ($Y = M\Omega^T$) like this:

```python
import numpy as np
import cvxpy as cp

def houseman_deconvolute(bulk_beta, reference_matrix):
    """
    Python implementation of Houseman Constrained Quadratic Programming.
    bulk_beta: Array of shape (n_CpGs,) representing the patient sample
    reference_matrix: Array of shape (n_CpGs, n_cell_types) 
    """
    n_cells = reference_matrix.shape[1]
    omega = cp.Variable(n_cells)
    
    # Constraints: Proportions must be non-negative and sum to 1
    constraints = [omega >= 0, cp.sum(omega) == 1]
    
    # Objective: Minimize the sum of squared residuals
    objective = cp.Minimize(cp.sum_squares(reference_matrix @ omega - bulk_beta))
    
    problem = cp.Problem(objective, constraints)
    problem.solve()
    
    return omega.value # Returns cell-type proportions array

```

## 2. Deep Learning / Multilayer Perceptron Classifiers: HiTAIC

If your ultimate goal isn't just counting T-cells but predicting whether the buffy coat or cellular fraction indicates a tumor classification, look at **HiTAIC** (Hierarchical Tumor Artificial Intelligence Classifier) (Zhang et al., 2023).

* **What it is:** Developed out of the same core academic environments as HiTIMED, HiTAIC moves away from linear Houseman regression entirely and transitions to a Python-driven **Multilayer Perceptron (MLP) Neural Network** using selective DNA methylation libraries.
* **Why it matches:** It employs a hierarchical structure specifically trained on tumor-type discriminative CpGs to chart tissue configurations and tumor tracking with accuracies scaling past 96%.

## 3. High-Performance HDF5/BAM Native Processing: `wgbstools`

Since your engineering stack relies heavily on sub-chromosome `.h5` matrices and speed, look at the computational framework **`wgbstools`** (Loyfer, 2026).

* **What it is:** A highly optimized computational suite specifically designed for fast access and fragment-level indexing of high-throughput methylome sequencing data.
* **Why it matches your infrastructure:** It bypasses old micro-array assumptions and deals directly with BAM and fragment-level data configurations. It features native, automated commands for biomarker and differentially methylated block (DMB) identification, enabling fast genomic segmentation directly compatible with custom Python alignment scripts.

## 4. Cross-Modal Deconvolution Pipelines: STED

If you want to pull down complex cell-state properties out of bulk arrays by leveraging external references, a state-of-the-art framework is **STED** (Single-cell Topic modeling and Epigenetic Deconvolution) (Liao, 2026).

* **What it is:** A flexible, Python-compatible probabilistic framework built for cross-modal cell-type deconvolution and signal inference.
* **How it works:** STED maps cell-type-specific references using a shared latent space framework, allowing you to run Bayesian inference matrices to estimate cell fractions and reconstruct exact cell-type-specific regulatory landscapes out of bulk data layers.

---

### Suggested Engineering Pivot

Given that you already have an optimized Python pipeline handling parallelized `{chr}-{ctx}.h5` HDF5 files:

1. **Do not back-port to R.** Avoid wrapping your code in R-based execution bridges (`rpy2`) to run old `EpiDISH` or `HiTIMED` scripts, as this will crush your parallel data streaming performance.
2. **Extract the reference matrix.** Download the validated CpG lookup indexes (the $M$ matrix) directly from the published HiTIMED or FlowSorted/EPIC data sets.
3. **Run deconvolution natively.** Stream your `.h5` arrays straight into an optimized vector processing loop in Python using **`cvxpy`** or **`scipy`** to calculate the local tissue proportions.

### Implementation status (MethylPipeline)

Both deconvolution modes ship in `packages/methyldeconv` behind the `pipeline.cell_deconvolution` action, selected by `actionConfig.cell_deconvolution.method`:

- **`houseman`** (default) — one flat SLSQP constrained projection against the committed FlowSorted.Blood.EPIC IDOL 6-cell basis (`houseman.py`).
- **`hitimed`** — an analyte-driven **hierarchical tree** of the *same* per-node QP (`hitimed.py`). Each internal node splits a parent compartment into children with a node-specific basis, and leaf proportions are the product of the path weights (renormalized to 1). The tree root is chosen from the project's `regulatory.primary_analyte`:
  - `buffy_coat` → immune/leukocyte subtree only (no tumor compartment; blood carries no tumor DNA). This subtree ships in the wheel, derived from the real IDOL basis.
  - `cfdna` → a `tumor_fraction` vs `non_tumor` (immune) top split from a dedicated plasma atlas, descending into the shared immune subtree. Plasma is mostly hematopoietic with a low ctDNA fraction, so tumor is a single lumped leaf exposing ctDNA burden as a covariate.
  - `tissue` → the full tumor / immune / stromal tree over the immune subtree.

Consistent with the pivot above, workers never run R: the cfDNA/tissue tumor bases are composed offline (`build_cfdna_atlas_basis.py`) from operator-supplied published atlases, and only the measured blood immune subtree is committed. Output is the same `cell_fractions.csv`, so ECDF/tabular covariate wiring is unchanged — the hierarchical leaves are consumed as an ALR composition (see `cell_deconv_hitimed.profile.json`). See [`docs/plans/hitimed-hierarchical-deconvolution.plan.md`](../plans/hitimed-hierarchical-deconvolution.plan.md).

### References

* Liao, Y. (2026). STED: flexible cross-modal topic modeling infers cell-type-specific regulatory landscapes from bulk epigenomics. *Briefings in Bioinformatics*, *27*(3), bbag347.
* Loyfer, N. (2026). wgbstools: a computational suite for DNA methylation sequencing data analysis. *Life Science Alliance*, *9*(4), e202503514.
* Zhang, Z., Lu, Y., Vosoughi, S., Levy, J. J., Christensen, B. C., & Salas, L. A. (2023). HiTAIC: hierarchical tumor artificial intelligence classifier traces tissue of origin and tumor type in primary and metastasized tumors using DNA methylation. *NAR Cancer*, *5*(2), zcad017. [https://doi.org/10.1093/narcan/zcad017](https://doi.org/10.1093/narcan/zcad017)
Cited by: 21
