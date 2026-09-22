# Prostate Cancer Detection doc — MethylPipeline fitness analysis

**Status:** research / design note (not operator runbook).  
**Source analyzed:** [`Prostate Cancer Detection.md`](Prostate Cancer Detection.md)  
**Related:** [`BuffyCoat_vs_cfDNA_for_Cancer_Detection.md`](BuffyCoat_vs_cfDNA_for_Cancer_Detection.md), SaMD SOP [`docs/usage/18-samd-study-lifecycle.md`](../usage/18-samd-study-lifecycle.md), evidence index [`docs/regulatory/validation-evidence-index.md`](../regulatory/validation-evidence-index.md)  
**Interactive canvas:** [pca-detection-fitness](../canvas/README.md#pca-detection-fitness) (sync with `bash scripts/sync_cursor_canvases.sh` to open beside chat)

**Date:** 2026-07-10  
**Verdict:** **adopt-with-caveats**

---

## Executive verdict

The clinical **gatekeeper** framing (high NPV for Gleason ≥3+4 / GG≥2, separate indolent GG1) and the critique of **buffy-only / shallow WGBS** for early localized csPCa are scientifically directionally correct and align with MethylPipeline’s own analyte research. The wet-lab SOW (EM-seq + hybrid capture) and pan-cancer “hierarchical NN” are **not** “apply with few/no pipeline changes”—they are mostly upstream assay design and future ML architecture. MethylPipeline can support the **software/evidence half** of a gatekeeper program today via the SaMD ladder + staged cohorts + clinical metrics, but only after analyte/endpoint redesign and a few small, disease-agnostic extensions.

---

## Assertion-by-assertion evaluation

| Claim | Support | Risk |
|-------|---------|------|
| Gatekeeper must prioritize **NPV ≥95% for csPCa (GG≥2)**, with sens/spec + prevalence | Strong clinical consensus for rule-out tests; doc correctly notes NPV prevalence dependence | 95% is a **clinical target**, not evidence MethylPipeline (or any assay) currently meets; must not become a Python default |
| Separate **GG1 (3+3) from GG2 (3+4)** | Clinically sound transition point | Biologically hard in liquid biopsy; current Buffy healthy-vs-PCa work does not test this |
| Estimate **pattern-4 % burden** | Plausible clinical utility; doc admits need for pathology validation | **Speculative** for cfDNA/urine methylation; continuous endpoint not in pipeline |
| **Spatial localization** for targeted biopsy | High clinical value for imaging | **Out of scope** for blood/urine methylation software; weak molecular claim |
| Localized PCa → **very low ctDNA fraction**; WGBS inefficient | Well-supported literature consensus | Implies assay redesign (depth/targeting), not just software knobs |
| **Buffy alone** unlikely to grade-discriminate | Aligns with `BuffyCoat_vs_cfDNA_for_Cancer_Detection.md` | Current Buffy SaMD research path is **host-response**, not gatekeeper-grade biology |
| Buffy as **matched hematopoietic control** | Sound (esp. mutation assays; useful for methylation confounding) | Needs paired study design; not automatic today |
| Prefer **targeted deep methylation** (urine/cfDNA) over WGBS for gatekeeper | Sound engineering for low TF | Wet-lab + CRO; pipeline consumes FASTQ→BAM→H5 regardless |
| Candidate genes (GSTP1, APC, RASSF1A, PITX2, …) | Reasonable literature candidates; doc labels them candidates | Must stay **study/profile priors**, never Python hardcoding |
| EM-seq + hybrid capture @ 2–5k× | Operationally plausible SOW | Outside MethylPipeline; chemistry is upstream |
| GRAIL = wide/shallow MCED vs narrow/deep gatekeeper | Directionally correct tradeoff | Some numbers (0.05% TF, “stellar” Stage I) are **marketing-grade speculation** |
| Modular pan-cancer via bigger probe set + TOO NN | Wet-lab modularity OK | “Hierarchical neural network” ≠ current MethylPipeline (ECDF/FeatureCuts/MC stability) |

---

## Recommendation feasibility matrix

| Recommendation | Feasibility in MethylPipeline |
|----------------|-------------------------------|
| Report NPV/PPV/sens/spec with CIs for intended-use population | **Already possible** — `packages/methylvalidation/methyl_validation/clinical_performance.py`, Wilson CIs; holdout Workflow 3 |
| Gleason-staged labels (3+3 … 4+5) as disease stages | **Config-only** — study manifest stages ([usage ch.16](../usage/16-tutorial-healthy-vs-cancer-stages.md)); SaMD staged program |
| SaMD evidence ladder before clinical claims | **Already possible** — `samd_research` → `samd_holdout_enrichment` → `samd_pivotal`; claims blocked pre-pivotal |
| Buffy research with `dual_fc` | **Already done** — Buffy → `samd_research` + `dual_fc` |
| Switch primary analyte to cfDNA + fragmentomics | **Config-only / ops** — `regulatory.primary_analyte: cfdna`, SamplePrep fragmentomics |
| Empirical panel via MC stability → freeze `fixed_dmp_panel` | **Already possible** — discovery path; compatible with capture BED if regions are sequenced |
| Define positive class as **csPCa (GG≥2)** for rule-out NPV (GG1 as non-positive) | **Small code** — today `screening_binary` is `control_vs_pooled_disease` only |
| Acceptance gate on **NPV LCB** (+ prevalence metadata) | **Small code** — only `min_sensitivity_lcb` / `min_specificity_lcb` exist today |
| High-NPV **operating-point** selection | **Small code** — metrics exist; rule-out threshold policy not first-class |
| Soft gene/region priors (BED/gene list) for enrichment/caps | **Small–medium code** — prefer profile/site overlays; do **not** hardcode GSTP1 et al. |
| Paired plasma + buffy background | **Config + study design** — analyte docs endorse; paired subtraction not a dedicated action |
| EM-seq / Twist-Agilent SOW | **Out of pipeline** — wet-lab/CRO |
| Pattern-4 % continuous model | **Large redesign** — new endpoint + labels + validation |
| Spatial lesion localization | **Reject for MethylPipeline** — imaging/other modality |
| Pan-cancer TOO hierarchical NN | **Large redesign** — not current architecture |
| New SQL NodeTypes for “prostate gatekeeper” | **Do not** — architecture forbids |

---

## MethylPipeline fitness gaps & proposed extensions

### What fits today (software/evidence)

- Disease-agnostic MC stability, FeatureCuts (`dual_fc`), enricher/PPI, freeze, locked model, PCCP scaffolds
- Multi-stage Gleason cohorts + OVR-style backends
- Clinical performance reports with NPV; partitions; SaMD claim gating
- cfDNA path with fragmentomics + analyte-match guards
- Evidence index under `docs/regulatory/` (Buffy package still stability-oriented / incomplete freeze→model for gatekeeper claims)

### Gaps vs the document’s ideal gatekeeper

1. **Wrong primary biology for current Buffy work** if the claim is pre-biopsy csPCa rule-out (doc + internal analyte research agree).
2. **Wrong screening definition** for gatekeeper NPV: pooled “any disease” ≠ GG≥2 positive.
3. **No NPV LCB / prevalence-conditioned acceptance** in config schema.
4. **No pattern-4 burden or spatial** outputs.
5. **Discovery assumes WGBS-style genome-wide HDF5**; targeted capture is upstream—pipeline can still run if extractor outputs compatible H5, but region-aware depth/QC assumptions may need profile tweaks.
6. Evidence packages lack holdouts / pivotal partitions for live prostate studies.

### Preferred extension style

Profile/site/program overlays + study manifests; workers keep consuming `resolvedConfig`; no disease-specific Python defaults; no new NodeTypes.

---

## What NOT to do

- Hardcode prostate genes, Gleason cutoffs, or “NPV≥0.95” into package `DEFAULT_*` / Pydantic defaults
- Create prostate-specific DomainPrograms or SQL NodeTypes
- Treat Buffy healthy-vs-PCa BA as gatekeeper clinical evidence
- Enable `allow_clinical_performance_claims` before `samd_pivotal` + populated `pivotal_validation`
- Bake GRAIL-comparison marketing numbers into acceptance criteria
- Implement spatial localization or pan-cancer TOO NN “because the doc said so”

---

## Top 5 actionable extensions (impact vs effort)

1. **Configurable clinical screening roles** (e.g. positive = GG≥2; GG1 as control-side or separate class) + report NPV for that definition — **high impact / small code**
2. **`min_npv_lcb` (+ optional prevalence / intended-use metadata) in validation schema + clinical_performance gates** — **high impact / small code**
3. **Stand up plasma (or urine) Gleason-staged study on `samd_research` + `dual_fc` with real `locked_test` partitions** — **high impact / config+ops** (largest real blocker is data, not code)
4. **Rule-out operating-point selection** (threshold policy optimizing NPV/sensitivity under prevalence assumptions) — **medium-high impact / small–medium code**
5. **Soft region/gene prior overlays** (BED or gene list via profile/site `actionConfig`) to bias discovery/enrichment toward candidate DMRs without hardcoding — **medium impact / small–medium code**

Honorable mention: paired `cfdna`+`buffy_coat` manifests for background control (config/study design).

---

## Open questions (product / study design)

1. Is the near-term intended use still **buffy host-response**, or a commitment to a **pre-biopsy gatekeeper** claim (requires plasma/urine + pathology GG labels)?
2. Are there (or can we acquire) **pre-biopsy cohorts** with GG1 vs GG≥2 labels and patient-disjoint holdouts?
3. Will wet-lab move to **EM-seq + hybrid capture**, or stay **WGBS** for discovery then shrink the panel later?
4. Are **pattern-4 %** and **spatial localization** in-scope SaMD claims, or aspirational only?
5. For screening NPV: should **GG1 count as “negative”** (defer biopsy) or as a third category (not rule-out)?

---

## Architecture constraints (locked for any follow-on work)

- Disease-agnostic, config-not-code, four-layer config (site / profile / study / program)
- SaMD ladder: `samd_research` → `samd_holdout_enrichment` → `samd_pivotal` + `researchMode` overlays
- DomainPrograms are algorithm-generic fixtures (`mc_stability`, etc.); study facts in `/work/projects/...`
- Workers consume `resolvedConfig`; no new SQL NodeTypes for science
