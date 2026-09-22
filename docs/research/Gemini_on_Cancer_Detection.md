The Multi-Cancer Early Detection (MCED) and liquid biopsy landscape has evolved rapidly beyond GRAIL's Galleri test. While GRAIL popularized the use of cell-free DNA (cfDNA) methylation, competing companies are trying to improve early-stage sensitivity by using **multiomics** (combining methylation with fragmentomics, proteins, or RNA) or using **buffy-coat samples** as a filtering mechanism to eliminate false positives.

A breakdown of the prominent companies, their platforms, and the specific analytes they leverage reveals several distinct approaches:

---

## 1. Multiomic & Methylation-Focused Competitors (cfDNA)

These platforms compete directly in the MCED space, utilizing advanced sequencing techniques and machine learning to analyze plasma-derived cfDNA.

* **Exact Sciences (Cancerguard®):**
Developed in collaboration with the Mayo Clinic, the Cancerguard test targets the deadliest cancers (including pancreatic, lung, liver, and esophageal). Rather than relying solely on methylation, Exact Sciences uses a **multiomic approach** that combines cfDNA methylation, mutation data, and protein biomarkers to maximize sensitivity while keeping false-positive rates low.
* **Adela:**
Adela utilizes a proprietary **genome-wide methylome enrichment technology** (cfMeDIP-seq). Unlike GRAIL, which relies on chemical bisulfite conversion—a process that can degrade fragile cfDNA—Adela uses an affinity-based enrichment method to capture highly detailed methylation signals without destroying the underlying DNA fragments. They target both multi-cancer early detection and Molecular Residual Disease (MRD) monitoring.
* **Freenome:**
Freenome emphasizes that a single analyte cannot completely capture tumor heterogeneity. Their platform evaluates signals along the entire central dogma: **cfDNA fragments (fragmentomics), methylation patterns, cell-free RNA (cfRNA), and circulating proteins**, alongside immune-profiling. They have collaborated with NVIDIA to build open-source deep learning foundation models trained to decode these intricate cfDNA signatures.
* **Gene Solutions (SPOT-MAS 10):**
A prominent player in the Asian market that received an FDA Breakthrough Device Designation. It is a multi-omics test analyzing **cfDNA methylation alongside fragmentomic signatures** (analyzing the specific sizes and end-motifs of chopped-up DNA strands floating in the plasma) via machine learning to screen for 10 common and aggressive cancers.

---

## 2. The Role of the Buffy-Coat (White Blood Cells)

The buffy-coat layer of a centrifuged blood sample consists of white blood cells (leukocytes) and platelets. In early-detection liquid biopsies, buffy-coat is primarily utilized as a **critical filter for biological noise**, rather than a standalone analyte for finding tumor DNA.

> **The "CHIP" Problem:** As humans age, stem cells in the bone marrow naturally acquire somatic mutations. This phenomenon is known as **Clonal Hematopoiesis of Indeterminate Potential (CHIP)**. These mutated white blood cells shed their own mutated DNA into the bloodstream.

If a liquid biopsy assay relies purely on looking for cancer-associated mutations in cfDNA, it can easily mistake a harmless CHIP mutation for an early-stage solid tumor, resulting in a terrifying false positive.

* **Guardant Health (Guardant360 / Guardant Shield):** Guardant routinely sequences the patient's **buffy-coat alongside the plasma cfDNA**. By matching the mutations found in the plasma against the genomic data found in the buffy-coat, their bioinformatics pipeline filters out mutations originating from normal blood cells, ensuring the reported mutations are genuinely tumor-derived.
* **Freenome & Exact Sciences:** Both utilize germline DNA derived from white blood cells (buffy-coat) during their assay validation and clinical pipeline development to map out background noise and focus machine learning models exclusively on true malignancy signals.

---

## 3. Alternative Analytes & Niche Platforms

Other innovators are looking entirely outside of plasma cfDNA to capture early-stage cancer markers:

| Company / Institution | Primary Analyte(s) | Methodology & Target |
| --- | --- | --- |
| **Lucence (LiquidHallmark)** | ctDNA Mutations + Amplicon NGS | Uses ultrasensitive amplicon sequencing to look for highly specific cancer mutations across dozens of cancer types. |
| **Thrive Early Detection** *(Acquired by Exact Sciences)* | cfDNA Mutations + Proteins | Developed the "CancerSEEK" paradigm, utilizing a focused panel of DNA mutations coupled with specific protein driver markers. |
| **Extracellular Vesicle Pioneers** *(Various Academic/Biotech Hubs)* | Exosomes / Extracellular Vesicles (EVs) | Isolating lipid-bound vesicles shed by tumors. EVs contain protected cargo (mRNA, microRNA, and proteins) that directly reflect the cell of origin. |
| **Delfi Diagnostics** | cfDNA Fragmentomics | Uses low-coverage whole-genome sequencing to map the fragmentation patterns of cfDNA. Because tumor DNA is packaged differently than healthy DNA, its "breakage" patterns form a unique signature. |
