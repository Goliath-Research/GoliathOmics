# Executive Opportunity Brief: Florida Community-Access Partnership

**Presenting organization:** Goliath Research Inc. (Winter Haven, FL)  
**Product platform:** **GoliathOmics** (powered by *GoliathWorkflow*, *mojo-align*, and *MethylExtractor*)  
**Architecture pillar:** 100% free & open-source toolchain (PostgreSQL and auditable open languages)  
**Initiative:** Statewide community-access software partnership & joint Florida/federal grant strategy  
**Intended partners:** UF Health Cancer Center (HiPerGator), Sylvester Comprehensive Cancer Center (Pegasus), AdventHealth (Winter Haven → Orlando TRI) as clinical/equity arm  
**Not the lead IT partner:** Moffitt’s commercial OCI–NVIDIA–Deloitte shared fabric (see internal [florida-hub-partnership-assessment.md](florida-hub-partnership-assessment.md))  
**Date:** September 15, 2026  
**Science note:** Native EM-Seq **MHB/MHL + overall-survival** (`cfdna_emseq_mhl_survival`) implements the *published* Wong/Wang 2026 method class; that is not a Moffitt enterprise deployment.

---

## 1. Executive Summary & Strategic Vision

### The Mission
Goliath Research Inc., based in **Winter Haven**, is offering **GoliathOmics** as an open, community-accessible multiomics platform so that **grant dollars buy sequencing and care, not software seats**. The goal is statewide access at the lowest possible cost—especially in Polk / Heartland counties that sit between NCI centers but do not share Tampa’s enterprise cloud budget.

We seek **academic HPC hosts** that already paid for GPUs (UF HiPerGator, Sylvester Pegasus) and a **local clinical arm** (AdventHealth Winter Haven → Orlando Translational Research Institute). We are **not** proposing to replace Moffitt’s OCI–NVIDIA–Deloitte fabric, ThinkHub tumor boards, or digital-pathology stack.

### The Problem in Cancer Genomics & Early Detection
Modern oncology diagnostics increasingly require combining multiple genomic modalities—liquid biopsy cell-free DNA (cfDNA) methylation, whole-genome bisulfite sequencing (WGBS), enzymatic methylation sequencing (EM-Seq), RNA-Seq transcriptomics, and mass-spectrometry proteomics. However, clinical translation is severely hindered by three universal bottlenecks:
1. **Computational Inefficiency & Modality Sprawl:** Each omics assay traditionally requires a bespoke software stack, multiplying compute costs and fragmenting bioinformatics workflows across disconnected pipelines.
2. **Proprietary Software Lock-In & Budget Drain:** Commercial diagnostic platforms often saddle academic medical centers with expensive proprietary database licenses and closed-source runtimes, diverting grant dollars away from clinical sequencing.
3. **Methodological Artifacts & Data Leakage:** Paired-end read-overlap double counting, reference bias in linear genome alignment, and accidental mixing of training and holdout patients ("data leakage") lead to irreproducible biomarker claims.
4. **The "FDA Chasm":** Academic discoveries frequently stall because research pipelines lack the auditability, configuration locking, patient-partition enforcement, and traceability needed for CLIA validation and FDA Software as a Medical Device (SaMD) regulatory clearance.

### The Solution: GoliathOmics with a 100% Free Toolchain Foundation
**GoliathOmics** is an enterprise-grade, regulatory-ready platform designed for comprehensive genomics and multiomics diagnostic development. It is built entirely on a **free and open-source toolchain**:
* **PostgreSQL as the Single, Unified Database:** All database operations (configuration, task scheduling, leases, audit logs) run on open-source **PostgreSQL**. There are no recurring database license fees. The same engine deploys on bare-metal institutional HPC clusters, private servers, or any cloud (AWS, GCP, Azure).
* **Open Language Stack:** The platform is written in modern, auditable, open-source languages: **Python**, **Mojo**, native **C / HTSlib**, and **TypeScript**.
* **Packs Architecture for "Anything Genomics":** Couples generalized genomics services with modular **Process Packs** (DNA Methylation, RNA-Seq, Proteomics, Variant Calling) on a unified modeling plane.
* **Backend Database & Orchestration Engine (`GoliathWorkflow`):** The distributed execution engine managing DomainProgram compilation, task queues, lease recovery, Content-Addressed Action Store (CAAS) caching, and SaMD claim gates over PostgreSQL.
* **Open-source alignment and extraction (published method class, MIT):**
  * **`mojo-align`**: Native-Mojo multi-GPU alignment engine (linear path follows **bwa-meth**; pangenome path follows methylGrapher / vg giraffe) delivering pangenome graph alignment (eliminating reference bias across diverse Florida populations) on NVIDIA CUDA and AMD ROCm.
  * **`MethylExtractor`**: MIT fork of **MethylDackel** — high-throughput C cytosine extractor with coordinate-based paired-end mate clipping to eliminate double-counting artifacts, writing high-density chunked Zstd HDF5 storage.

```mermaid
flowchart TB
    subgraph Client_Layer["Clinical & Research Users (Florida HPC + community hospitals)"]
        UI["EpiPortal Web Interface · REST API Services · CLI Administration"]
    end

    subgraph GoliathOmics_Platform["<b>GoliathOmics Platform Layer</b> (Exposed Services for Anything Genomics)"]
        direction TB
        subgraph Packs["Modular Packs Architecture"]
            direction LR
            P_METH["<b>DNA Methylation Pack</b><br/>WGBS · EM-Seq · cfDNA<br/>FeatureCuts · MHB/MHL + Cox"]
            P_RNA["<b>RNA-Seq Pack</b><br/>STAR / Kallisto Quant<br/>Expression & DE Panels"]
            P_PROT["<b>Proteomics Pack</b><br/>DIA-NN · Sage · Prosit<br/>Mass-Spec Feature Seam"]
            P_VAR["<b>Genomic Variant Pack</b><br/>Somatic / Germline SNVs<br/>Fragmentomics & CNV"]
        end
        FEAT_SEAM["<b>Unified Feature Seam</b> (samples × features tabular modeling)"]
        Packs --> FEAT_SEAM
    end

    subgraph Backend_Engine["<b>GoliathWorkflow</b> Engine · Open-Source PostgreSQL"]
        direction LR
        DB[("<b>PostgreSQL Single DB</b><br/><code>cfg</code> & <code>wf</code> Schemas<br/><i>(Free · Open-Source · Portable)</i>")]
        GW_ENG["Distributed Gateway & Scheduler<br/>DomainProgram Compiler"]
        CAAS["CAAS Content-Addressed Store<br/>Zero-Redundancy Compute"]
        SAMD["SaMD State Machine<br/>Claim Gates & Partitions"]
        DB <--> GW_ENG <--> CAAS <--> SAMD
    end

    subgraph Compute_Hardware["High-Performance Execution Layer"]
        MA["<b>mojo-align</b><br/>GPU Pangenome & Linear Aligner<br/><i>(NVIDIA CUDA & AMD ROCm)</i>"]
        ME["<b>MethylExtractor</b><br/>High-Throughput C Extractor<br/><i>(Coordinate Clip & Zstd HDF5)</i>"]
        WORKERS["Stateless GPU/CPU Workers<br/><i>(HiPerGator · Pegasus · hospital BYOC)</i>"]
    end

    UI --> GoliathOmics_Platform
    GoliathOmics_Platform <--> Backend_Engine
    Backend_Engine --> Compute_Hardware
```

---

## 2. Core Architectural Pillars: Free Toolchain & Zero Vendor Lock-In

### 1. PostgreSQL: Zero License Fees, Maximum Data Sovereignty
* **Single Database Architecture:** GoliathWorkflow consolidates all system schemas (`cfg` configuration registry, `wf` workflow orchestration, worker leases, node executions, and audit records) exclusively onto **PostgreSQL**.
* **No proprietary database licenses:** Partners are not billed for commercial database seats; PostgreSQL is free to run at any scale the institution already operates.
* **True Deployment Agnosticism (BYOC):** PostgreSQL runs identically on UF HiPerGator or Sylvester Pegasus partitions, a community-hospital Linux GPU, or any cloud (AWS, GCP, Azure, OCI). Sensitive clinical sequencing data remains inside the **host institution’s** boundary—we do not require a new commercial AI tenancy.

### 2. Open-Source Codebase
* **Auditability & Community Maintenance:** The entire codebase is implemented in open, transparent, and reproducible languages:
  * **Python:** Clean Pydantic data schemas, statistical validation, and orchestration compiler.
  * **Mojo:** Next-generation systems programming language for GPU kernels and pangenome graph alignment.
  * **C / HTSlib:** High-performance cytosine calling and binary compression.
  * **TypeScript / React:** Clean, responsive clinical and laboratory operator interfaces.
* **Grant Budget Optimization:** Because the entire software stack is built on free and open-source foundations, **100% of grant funding** (from the Florida Department of Health or NIH) goes directly toward clinical personnel, biobank retrieval, wet-lab reagents, and compute—with zero budget wasted on commercial software licenses.

---

## 3. Platform Architecture: The "Packs" Ecosystem for Anything Genomics

GoliathOmics avoids the trap of single-assay monolithic software by organizing science into three decoupled layers: **Process Packs**, **Assay Procedure Packs**, and **Application Packs**.

```mermaid
flowchart LR
    subgraph Process["1. Process Packs (Modality)"]
        METH["Methylation<br/>(WGBS / EM-Seq)"]
        RNA["RNA-Seq<br/>(Expression / DE)"]
        PROT["Proteomics<br/>(DIA-NN / Sage)"]
    end

    subgraph Procedure["2. Assay Procedure Packs (Protocol)"]
        BUFFY["buffy_wgbs_pangenome_gene_fc"]
        CFDNA["cfdna_wgbs_plasma"]
        EMSEQ["cfdna_emseq_targeted"]
        MHL["cfdna_emseq_mhl_survival"]
        RNA_PROC["rna_parabricks_star_de"]
    end

    subgraph Application["3. Application Packs (Indication)"]
        PROST["Prostate Cancer Gatekeeper"]
        MCRPC["mCRPC OS Prognosis<br/>(Wong / Wang method class)"]
        MCED["Multi-Cancer Liquid Biopsy"]
        AD["Alzheimer cfDNA"]
    end

    METH --> BUFFY & CFDNA & EMSEQ & MHL
    RNA --> RNA_PROC
    BUFFY --> PROST
    CFDNA --> PROST & MCED & AD
    EMSEQ --> PROST & MCED
    MHL --> MCRPC
```

### The Three-Layer Packs Model
1. **Process Packs (Omics Modalities):** Defines data structures, QC contracts, and typed actions for an analytical modality:
   * **DNA Methylation Pack:** FASTQ ingress, pangenome/linear bisulfite alignment, coordinate-clipped extraction, CpG/CHG/CHH methylation metrics, and read-level co-methylation pattern tiles.
   * **RNA-Seq Transcriptomics Pack:** GPU-accelerated STAR alignment (`sample.parabricks_rna_fq2bam`) or pseudo-alignment (`kallisto`), RNA QC contracts, differential expression (DE) feature selection, and tabular integration.
   * **Mass-Spectrometry Proteomics Pack:** GPU-accelerated DIA-NN, CPU Sage DDA, Prosit/Casanovo spectral prediction, and protein matrix ingestion.
   * **Multiomics Seam:** Joins disparate data streams into a standardized `samples × features` modeling layer for unified machine learning classification.
2. **Assay Procedure Packs (Protocols & Recipes):** Curated, versioned configurations that specify exactly how a sample matrix is sequenced and scored without modifying underlying code:
   * `cfdna_wgbs_plasma`: Plasma cfDNA whole-genome bisulfite sequencing with integrated fragmentomics.
   * `buffy_wgbs_pangenome_gene_fc`: Peripheral blood buffy coat WGBS using pangenome graph alignment and Houseman/HiTIMED immune cell deconvolution.
   * `cfdna_emseq_targeted`: Targeted deep enzymatic capture (2,000×–5,000× depth) using operator-supplied BED panels and coverage capping — **gene FeatureCuts / ECDF classification**.
   * `cfdna_emseq_mhl_survival`: Same EM-Seq SamplePrep; native methylation-haplotype-block (MHB) discovery or locked BED, Guo/Wong **methylation haplotype load (MHL)**, and **Cox / KM / time-AUC** (optional nomogram). Does **not** replace FeatureCuts; it is the method class in Wong et al., *npj Precis Oncol* (2026) 10:29 (published from the Wang laboratory). Open implementation for any Florida PI—not a Moffitt IT integration.
3. **Application Packs (Clinical Indications & Disease Overlays):** Lightweight configuration overlays applying clinical cohorts, stage hierarchies, and regulatory settings for specific diseases (e.g., Prostate Cancer Gatekeeper, **mCRPC overall-survival prognosis**, Alzheimer cfDNA, Pan-Cancer Early Detection) with **zero code changes**.

---

## 4. The Product Suite: Core Technical Attributes & Differentiators

| Product / Component | Architectural Layer | Primary Technology | Key Differentiators | Clinical & Operational Impact |
| :--- | :--- | :--- | :--- | :--- |
| **`GoliathOmics`** | **Clinical & Scientific Platform Layer** | TypeScript, Python, OpenAPI, Pydantic | • Modular Packs Architecture (Methylation, RNA-Seq, Proteomics)<br/>• Unified multiomics `samples × features` modeling seam<br/>• General-purpose genomics services with automated clinical reporting | • Eliminates modality sprawl; unifies multiomics under one roof<br/>• Deploys new cancer indications via config overlays in hours |
| **`GoliathWorkflow`** | **Backend Database & Engine** | **Single PostgreSQL Database** (`cfg` & `wf`), REST Gateway | • **100% free / open-source toolchain**<br/>• Four-layer configuration hierarchy (Site → Profile → Study → Program)<br/>• Monte Carlo stability selection & biological FeatureCuts<br/>• Content-Addressed Action Store (CAAS) for zero-redundancy compute<br/>• Built-in FDA SaMD evidence ladder with code-enforced claim gates | • Zero database license fees; runs on any cloud or local HPC<br/>• Enforces strict reproducibility and zero data leakage<br/>• Slashes compute costs via CAAS caching across large cohorts<br/>• Auto-assembles FDA 510(k)/De Novo submission scaffolds |
| **`mojo-align`** | **Open-source GPU alignment** | Modular **Mojo**, CUDA/ROCm DeviceContext, SIMD (**MIT**; bwa-meth / methylGrapher lineage) | • Dual-graph pangenome WGBS (`MojoGiraffe` C2T ∥ G2A)<br/>• Linear WGBS `fq2bam-meth` matching/beating Clara Parabricks<br/>• Zero-dialect portability across NVIDIA CUDA & AMD ROCm | • Eliminates reference bias across diverse Florida demographics<br/>• Slashes alignment wall-clock time and cloud GPU costs by >60% |
| **`MethylExtractor`** | **Open-source extraction & QC** | Native **C / HTSlib**, OpenMP, HDF5, Zstd (**MIT** MethylDackel fork) | • Coordinate-based overlapping read pair clipping<br/>• Compact 12-byte on-disk record with Zstd chunking<br/>• Native extraction manifests & context QC sidecars | • Completely removes artificial double-counting in paired-end WGBS<br/>• Slashes storage footprint by >70% compared to bedGraph/TSV |

---

### Deep Dive 1: `mojo-align` — Next-Generation Multi-GPU Sequence Alignment
Traditional bisulfite aligners (e.g., Bismark, BWA-meth) are CPU-bound, taking 15–30 hours per 30× human genome. While proprietary accelerators exist (such as NVIDIA Clara Parabricks), they enforce vendor lock-in and cannot perform graph-aware pangenome bisulfite alignment.

* **Dual-Graph Pangenome Alignment (`MojoGiraffe`):** Implements dual C2T and G2A graph streaming alignment over GBZ pangenome reference graphs (e.g., Human Pangenome Reference Consortium). It maps bisulfite reads directly to variation graphs, emitting graph-aware GAF alignments that completely resolve structural variation bias and mapping dropouts in repetitive or hypervariable cancer loci.
* **Portable Linear Acceleration (`fq2bam-meth`):** Open-source Mojo implementation of the published **bwa-meth** linear WGBS method. It operates across NVIDIA GPUs (sm_90/GH200/H100) and AMD Instinct GPUs (MI300X) through a unified DeviceContext abstraction, streaming BAMs directly into memory-managed sorting, mate-fixing, and duplicate marking.
* **Automated Memory-Aware Tiling:** Dynamically sizes GPU allocations and uncompressed BAM buffers against available hardware High Bandwidth Memory (HBM), enabling seamless execution across varying hardware tiers without out-of-memory crashes.

### Deep Dive 2: `MethylExtractor` — Deterministic, High-Throughput Cytosine Extraction
`MethylExtractor` is an MIT **MethylDackel** fork (Devon P. Ryan), not a closed-source extractor. Methylation calling from bisulfite BAM files is notoriously vulnerable to technical artifacts that distort downstream statistical classifiers.

* **Coordinate-Based Overlapping Mate Clipping:** In paired-end sequencing, paired reads frequently overlap in the middle of sequenced fragments. Standard extraction tools double-count cytosines in these overlapping segments, falsely inflating coverage and skewing methylation ratios. `MethylExtractor` implements precise coordinate-based clipping (`bases_skipped_overlap_clip`), ensuring every genomic cytosine is counted exactly once per sequenced molecule.
* **Optimized 12-Byte Binary Record & Zstd HDF5:** Instead of bloated, multi-gigabyte text bedGraph files, `MethylExtractor` structures output into chunked HDF5 containers utilizing a 12-byte binary record per site (`genomic_pos: uint32`, `mC: uint16`, `uC: uint16`, `tnc: uint8`). Integrated Zstd compression provides rapid streaming I/O for cohort-wide discovery.
* **Rigorous Quality Gate Integration:** Natively enforces MAPQ ≥ 30, Phred quality ≥ 20, coverage capping (`-C`) to counteract PCR bias, and emits comprehensive sample-level JSON manifests consumed directly by automated pipeline quality guardrails (`methylextractionqc`).

### Deep Dive 3: `GoliathWorkflow` — SaMD-Ready Distributed Workflow & Database Engine
`GoliathWorkflow` serves as the robust backend database and execution plane for GoliathOmics, built from inception around software as a medical device (SaMD) principles:

* **Single PostgreSQL Database Engine:** One open-source database for configuration and orchestration, with rock-solid ACID transactions, advisory locking, and clean schema migrations.
* **DomainProgram Intermediate Representation (IR):** Workflows are declared as structured JSON graphs supporting typed actions, loops (`foreach`), branching (`if/then/else`), multi-way dispatch (`switch`), and concurrent branch execution.
* **Strict Four-Layer Configuration Hierarchy:** Configuration is resolved across Site, Profile, Study Manifest, and Program layers. Tunable clinical parameters are declared strictly in configuration schemas—**never hardcoded as constants**.
* **Monte Carlo Stability Selection & Biological FeatureCuts:** Rather than selecting biomarkers from a single overfit training split, the engine runs Monte Carlo resampling cross-validation, computing recurrence frequencies and biological relevance (Storey FDR, STRING PPI, Enrichr pathways) to isolate invariant, stable biomarker signatures.
* **Content-Addressed Action Store (CAAS):** Computes cryptographic signatures of all task inputs, parameters, and container environments. When downstream machine learning models or classification thresholds are modified, CAAS instantly reuses upstream alignment and extraction artifacts, saving hundreds of compute hours across cohort studies.

---

## 5. Clinical & Cancer Focus: Early Detection & Gatekeeper Diagnostics

### Liquid Biopsy & Low-Tumor Fraction Challenges
Early-stage cancer detection in peripheral blood (cfDNA) operates at the biological limit of detection: circulating tumor fractions are frequently below 0.1% to 1.0%. GoliathOmics is specifically configured to overcome this barrier:
* **Host Immune Response & ctDNA Synergy:** Analyte-aware configurations support both whole-blood buffy coat profiling (capturing systemic immune and epigenetic remodeling) and plasma cfDNA (capturing tumor-derived hypomethylation and hypermethylated promoter marks).
* **Fragmentomics Integration:** Integrates fragment length profiles, end-motif frequencies, and copy-number variation alongside methylation signals to boost sensitivity in ultra-low ctDNA regimes.
* **EM-Seq & Targeted Deep Hybrid Capture:** Natively supports high-depth enzymatic methylation capture protocols (2,000×–5,000× depth), applying panel-specific BED filtering and coverage capping to optimize diagnostic yield.

### Method-level alignment with published Wong / Wang 2026 science

The published plasma cfDNA mCRPC work is **haplotype-block MHL + time-to-event modeling**, not mean-methylation FeatureCuts. GoliathOmics runs that **method class** as a first-class procedure so Florida PIs can use the paper’s science on an open engine without a classifier bake-off and **without** joining Moffitt’s commercial cloud.

| Wong et al. 2026 step | GoliathOmics today |
| :--- | :--- |
| EM-Seq + Twist capture BAM | Linear align (Parabricks `fq2bam_meth` or `mojo-align`); methylation BAM tags; operator `target_panel_bed` |
| mHapSuite haplotypes | Same extract pass: `{chrom}-CG.mhap.h5` (native store; not a mHapSuite wrap) |
| MHBDiscovery (window 3, \(r^2>0.3\), \(p<0.05\)) | `pipeline.mhb_mhl` with those defaults, or a **locked** operator MHB BED |
| MHL lengths 1–10; ≥3 CpGs; median reads >50 | Native MHL (`mhl_max_length` 10); same filter knobs |
| Cox PH, KM, time-dependent ROC, nomogram | `backend_profiles.cox` + study `survival_path` (time, event, optional PSA/ALP/LDH/ctDNA) |
| Nested test vs labs / predicted ctDNA | Optional nested LRT when those columns are on the sidecar |

**What a collaborating lab still supplies (not hardcoded in product):** capture panel BED and control regions, OS/labs dictionary, optional locked 15-MHB gene list. GREAT and an in-process ctdna.org API remain optional follow-ons. InformME entropy tiles and HiTIMED tumor fraction are **not** substitutes for MHL or the paper’s clinical ctDNA predictor.

This path does **not** claim a rerun of the published 96-patient nomogram. A UF or Sylvester pilot can compare native MHL/Cox to mHapSuite+R on a shared subset.

### Prostate & Solid Tumor "Gatekeeper" Paradigms
A prime translational target for **Central Florida community sites and academic HPC partners** is non-invasive **"gatekeeper" rule-out diagnostics** to spare patients from unnecessary invasive biopsies:
* **High Negative Predictive Value (NPV ≥ 95%):** Configured to optimize lower confidence bounds (LCB) for NPV in intended-use screening populations, separating indolent conditions (e.g., Gleason Grade 1 / 3+3) from clinically significant disease (Gleason Grade ≥ 2 / 3+4).
* **Disease Progression & Staged Modeling:** Built-in disease progression actions trace trajectory shifts across pre-malignant, early-stage, and metastatic phenotypes rather than simplistic binary distinctions.
* **Prognosis vs detection (keep the labels distinct):** Gatekeeper NPV (FeatureCuts on `cfdna_emseq_targeted`) and mCRPC overall-survival nomograms (MHL on `cfdna_emseq_mhl_survival`) are **separate intended uses**. Partners can run both; they must not be scored as if they were the same study.

---

## 6. Built-in SaMD & Regulatory Rigor: Solving the "FDA Chasm"

Academic software almost universally fails regulatory review because the development history lacks verifiable traceability. GoliathOmics and its GoliathWorkflow backend satisfy FDA 21 CFR Part 820, ISO 13485, and Good Machine Learning Practice (GMLP) requirements.

```mermaid
flowchart TD
    subgraph Ladder["Three-Profile SaMD Lifecycle Ladder"]
        P1["<b>1. samd_research</b><br/>Exploratory discovery · Hyperparameter tuning<br/>Patient-disjoint partitions recommended"]
        P2["<b>2. samd_holdout_enrichment</b><br/>Locked hyperparameters · Strict locked_test holdout<br/>Freeze biological panel · Reproducibility check"]
        P3["<b>3. samd_pivotal</b><br/>Pivotal validation cohort · Sealed independent test<br/><b>Code-enforced claim gate unlocked</b>"]
        P1 --> P2 --> P3
    end

    subgraph Scaffolds["Automated Regulatory Evidence Packages"]
        D1["<b>Release Identity:</b> Docker & Git SHA hashes"]
        D2["<b>Data Provenance:</b> Non-overlapping partition IDs"]
        D3["<b>Analytical Verification:</b> Monte Carlo stability CIs"]
        D4["<b>Clinical Metrics:</b> Wilson LCB for Sensitivity, Specificity, NPV"]
        D5["<b>FDA Submission Scaffolds:</b> Auto-generated 510(k) / De Novo topic maps"]
        P3 --> D1 & D2 & D3 & D4 & D5
    end
```

### Architectural Controls & Quality Assurances
1. **Code-Enforced Claim Gates:** The platform actively blocks clinical performance claims in software output if the study configuration is set to exploratory or research stages. Clinical claim generation is permitted *only* when executed under `samd_pivotal` against declared, untouched holdout partitions.
2. **Strict Patient-Disjoint Partition Enforcement:** Enforces patient-level independence keys across all splits, mathematically preventing longitudinal or multi-sample data leakage between training and validation cohorts.
3. **Immutable Audit Trails (`.action_results`):** Every node execution outputs a cryptographic manifest capturing input hashes, tool revisions, hardware flags, random seeds, and execution durations.
4. **Automated Submission Scaffolding:** Natively generates pre-formatted documentation mapping analytical and clinical results directly into FDA 510(k), De Novo, and CLIA analytical validation topic formats.

---

## 7. Strategic Alignment & Joint Funding Roadmap

**UF or Sylvester** is the grant-eligible cancer-institute lead and HPC host. **Goliath Research (Winter Haven)** is the technology co-investigator. **AdventHealth (Winter Haven / Orlando TRI)** is the Polk–Central Florida clinical and community-access arm. Compute runs as **BYOC on HiPerGator or Pegasus** (or a small hospital GPU)—not as a new commercial AI tenancy.

Mayo Florida (GCP enterprise) and Moffitt (OCI enterprise) are **not** lead IT partners; see [florida-hub-partnership-assessment.md](florida-hub-partnership-assessment.md).

### Funding Target 1: Florida Cancer Innovation Fund (FCIF) — FY 2026–2027 (Immediate Priority)
* **Funding Available:** ~$70 Million total state appropriation; up to **$2,000,000** per award for a 12-month project.
* **Upcoming Deadlines:** **Period 2: October 22, 2026**; Period 3: December 18, 2026.
* **Target Categories:** *Rapid Translation Grant* or *High-Impact Pilot Grant* (Novel Technologies for Diagnosis).
* **Strategic Positioning:**
  * **Lead Applicant:** UF Health Cancer Center **or** Sylvester Comprehensive Cancer Center (licensed Florida cancer institute; HPC already in place).
  * **Technology co-investigator:** Goliath Research Inc. (Winter Haven).
  * **Clinical / equity arm:** AdventHealth Winter Haven and Orlando TRI (Polk / Heartland access).
  * **Project Concept:** *"Open-toolchain, pangenome-aware liquid biopsy for underserved Central Florida populations, hosted on academic HPC."* Optional published-method arm: MHL+OS concordance (`cfdna_emseq_mhl_survival`) as science, not as a FeatureCuts substitute.
  * **Zero Software Licensing Overhead:** PostgreSQL and open tools so the request funds personnel, sequencing, and HPC queue time—not seats or OCI markup.
  * **Measurable 12-Month Deliverables:** Deploy GoliathOmics on HiPerGator or Pegasus; process a Polk/Central Florida retrospective cfDNA or WGBS set via AdventHealth; establish locked research metrics with Wilson CIs; document $0 software licenses.

### Funding Target 2: Bankhead-Coley Cancer Research Program (FY 2026–2027)
* **Funding Available:** $600,000 to $1,500,000 over 36–48 months.
* **Target Mechanism:** *Technology Transfer Feasibility* or *Discovery Science*.
* **Focus:** Deepening clinical validation of early-detection markers (e.g., localized prostate cancer rule-out or lung cancer liquid biopsy) using UF/Sylvester science cores and AdventHealth residual or prospective samples.

### Funding Target 3: Federal NIH / NCI Mechanisms (Parallel Pipeline)
* **NCI Informatics Technology for Cancer Research (ITCR U01/U24):** Open-access informatics platforms—natural fit for UF or Sylvester as academic home.
* **NIH/NCI Academic–Industrial Partnerships (PAR-25-338):** Only if the academic lead is UF or Sylvester (not an OCI/GCP enterprise tenancy).
* **SBA SBIR / STTR Fast-Track (Phase I/II):** Non-dilutive validation funding (up to $2.4M).

---

## 8. Proposed Collaboration & Governance Model

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                               COLLABORATION FRAMEWORK                                  │
├─────────────────────────────┬──────────────────────────────┬───────────────────────────┤
│ UF or Sylvester             │ Goliath Research (Winter     │ AdventHealth Winter Haven │
│                             │ Haven)                       │ / Orlando TRI             │
│ • FCIF-eligible lead / PI   │ • Technology co-investigator │ • Polk / Heartland access │
│ • HiPerGator or Pegasus GPU │ • GoliathOmics deploy &      │ • Residual / community    │
│ • Science cores, IRB home   │   SaMD evidence packaging    │   cohorts, local IRB      │
└─────────────────────────────┴──────────────────────────────┴───────────────────────────┘
                                           │
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                COMMUNITY BENEFIT                                       │
│ • 100% free toolchain: PostgreSQL, Python, Mojo, C — ZERO software seat licenses.      │
│ • Compute on academic HPC or hospital BYOC — not a new commercial AI tenancy.          │
│ • Data sovereignty: PHI stays in the host institution firewall.                        │
│ • IP: partners own clinical discoveries and panels; Goliath retains core software IP.  │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 9. Immediate Next Steps & Action Checklist

1. **Track A — grant-eligible HPC (this month, before FCIF Period 2):**
   * UF Bioinformatics / computational biology shared resource: HiPerGator queue pilot (20–50 de-identified samples; wall-clock vs nf-core/Bismark).
   * Sylvester Cancer Epigenetics / Precision Medicine: same pilot on Pegasus; pangenome + cfDNA story for South Florida diversity.
2. **Track B — local clinical access:**
   * AdventHealth Winter Haven and Orlando TRI: residual plasma, Polk equity narrative, community screening overlay.
3. **LOI / MOU:**
   * Name **UF or Sylvester** as corresponding PI / lead applicant; Goliath as technology co-I; AdventHealth as clinical site. Do not designate Moffitt Innovation as lead.
4. **Florida Cancer Innovation Fund (Period 2):**
   * **Deadline:** October 22, 2026.
   * Budget justification: **$0 software licenses**; HPC queue time + sequencing + Heartland access.
   * Specific aims: deploy on academic HPC; Polk/Central Florida cohort; locked research metrics.
5. **System staging:**
   * Runtime bundle on HiPerGator or Pegasus (Slurm-friendly workers), not an OCI tenancy.
   * Optional MHL concordance with a Florida PI using public/published methods (`cfdna_emseq_mhl_survival`)—not contingent on Moffitt enterprise IT.

---

### Contact Information

**Goliath Research Inc.**  
Winter Haven, Florida  
*Leadership & Technology Team*  
Email: partnerships@goliathresearch.com  
Web: [goliathresearch.com](https://goliathresearch.com)  
Platform: **GoliathOmics** (Powered by `GoliathWorkflow` [PostgreSQL], `MethylExtractor`, and `mojo-align`)  
