# Florida hub partnership assessment (internal)

**Status:** internal strategy note — not for external circulation.  
**Date:** 2026-09-15  
**Audience:** Goliath Research leadership.  
**Home:** Winter Haven, FL (Polk / Heartland).  
**External proposal this note retargets:** [florida-community-access-partnership-brief.md](florida-community-access-partnership-brief.md).  
**Related:** [GoliathOmics Gaps.md](GoliathOmics Gaps.md) (Wang/Moffitt *methods* completeness — science, not BD).

---

## Decision

**Do not pursue Moffitt as the IT, enterprise-AI, or FCIF lead partner.** Moffitt has already committed to a commercial shared fabric (Oracle Cloud Infrastructure bare-metal tenancy, NVIDIA NeMo/Clara, Deloitte implementation, plus ThinkHub tumor boards and Dicom/Aperio digital pathology). GoliathOmics’ offer — no seat licenses, PostgreSQL, run-anywhere GPUs, grant dollars to wet lab — **competes with that spend**. Chasing Office of Innovation / shared GPU tenancy wastes the October FCIF cycle.

**Do** keep citing **published** Wang/Wong method classes (EM-Seq MHB/MHL + Cox) as open science. Concordance does not require Moffitt’s ML department or OCI. See [GoliathOmics Gaps.md](GoliathOmics Gaps.md).

**Do** retarget community access to places that **cannot or will not** pay Moffitt-class OpEx: academic HPC (UF HiPerGator, Sylvester Pegasus) and Central Florida community systems (AdventHealth Winter Haven → Orlando TRI).

---

## Mission vs Moffitt’s stack

The brief’s public mission is **lowest-cost access for Florida’s population**, not “win Tampa’s enterprise AI RFP.”

| Moffitt shared technology | Relation to GoliathOmics |
|---------------------------|--------------------------|
| T1V ThinkHub (tumor boards) | Out of product scope |
| Dicom Unifier + Leica Aperio GT 450 | Out of product scope (PACS/pathology) |
| OCI + NVIDIA NeMo, clinical LLMs, trial matching, DRL adaptive therapy, radiomics | Adjacent hospital AI; **not** our pipeline. Do not pitch as a replacement. |

GoliathOmics is a **portable multiomics / SaMD-shaped science engine**. Pitching it as Moffitt’s shared platform is a category error.

---

## Florida institutions (fit for *our* offer)

Sources: public descriptions of each center’s compute posture (including the 2026-09-14 Gemini share *Moffitt Cancer Center Shared Technologies*) plus Goliath’s Winter Haven location.

| Institution | Compute posture | Fit for free/cheap toolchain | Role |
|-------------|-----------------|------------------------------|------|
| **UF Health Cancer Center / HiPerGator (Gainesville)** | On-prem DGX SuperPOD; CapEx already paid; GatorTron is *their* NLP, not a blocker for epigenomics cores | **High.** They want more samples per GPU-hour, not another cloud bill. | Preferred **FCIF/Bankhead-Coley eligible lead** + HPC host. Entry: Bioinformatics Core / computational biology shared resource. Hurdle: nf-core/Nextflow culture — show wall-clock vs Bismark/CPU. |
| **Sylvester / UM Frost IDSC Pegasus (Miami)** | Academic HPC partition, Slurm chargeback; epigenetics + liquid biopsy + diverse catchment | **Highest scientific fit** for pangenome + cfDNA methylation. | Co-lead or epigenetics PI. Entry: Cancer Epigenetics, Onco-Genomics SR, Precision Medicine. Hurdle: existing Nextflow/Snakemake. |
| **AdventHealth (Winter Haven → Orlando TRI / interventional genomics)** | Lean: Epic, Azure, FDA-cleared vendor AI. No supercomputer. | **Highest local / equity fit.** They buy turnkey tools *because* they cannot afford OCI. | Clinical + Polk/Heartland access arm. Not a sole NCI-style grant lead. |
| **Orlando Health** | Same lean-vendor pattern | Secondary clinical site | Implementation / cohort, not platform owner |
| **Mayo Clinic Florida (Jacksonville)** | Mayo Clinic Cloud on **GCP**, Rochester IT | **Low** as lead — same class of enterprise lock-in as Moffitt OCI | Skip unless an investigator-initiated PI comes to us |
| **Moffitt (Tampa)** | OCI OpEx + Deloitte + NVIDIA shared tenancy | **Wrong partner for deployment** | Do not chase Innovation/shared GPU. Optional: public methods only |

FCIF still wants a **licensed Florida cancer institute** as lead. That should be **UF or Sylvester**, not Moffitt. Goliath Research (Winter Haven) is the Florida technology co-investigator. AdventHealth is the **underserved-county clinical arm** (the equity story reviewers score).

---

## Geography (Winter Haven)

Polk / Heartland has high cancer burden and weaker NCI-center access than Tampa or Miami. **AdventHealth Winter Haven** is the local door into AdventHealth Cancer Institute / Translational Research Institute in Orlando (~1 hour). UF is ~2 hours north; Sylvester ~3 hours south. Compute should live on **their already-cheap HPC**, not a new commercial tenancy we ask them to buy.

Do **not** sell “we replace OCI.” Sell **BYOC on HiPerGator, Pegasus, or a small hospital GPU**.

---

## Two tracks (do not wait for one giant MOU)

**Track A — grant-eligible science (before FCIF Period 2, 22 Oct 2026)**  
UF Bioinformatics Core and/or Sylvester Cancer Epigenetics. Ask for a **pilot queue allocation** (not a cloud RFP): 20–50 de-identified cfDNA or WGBS samples; wall-clock vs nf-core/Bismark; **$0 software license** line in the budget.

**Track B — local access**  
AdventHealth Winter Haven / Lakeland Regional / county DOH. Residual plasma, community screening overlay, “compute in Florida, software free.” They do not need ThinkHub or Aperio.

**Wang methods as science, not Moffitt procurement**  
Offer `cfdna_emseq_mhl_survival` to UF/Sylvester PIs as an open implementation of a published method. Concordance with mHapSuite does not require Tampa enterprise IT.

---

## Grant aims (retargeted)

12-month FCIF example: deploy on **UF or Sylvester HPC**; process a **Polk/Central Florida** retrospective set via AdventHealth; publish locked metrics; document **$0 software licenses**. Drop “500–1,000 patients on Moffitt’s cluster.”

Federal: **ITCR U24** (open informatics) fits UF/Sylvester better than PAR-25-338 with Moffitt as the industrial-cloud site.

---

## What not to do

- Pitch GoliathOmics as Moffitt’s shared enterprise platform.
- Lead with tumor boards, digital pathology, NeMo, or trial-matching LLMs.
- Spend the October FCIF cycle on Moffitt Innovation if they will not leave OCI.
- Treat Mayo GCP the same way as Moffitt OCI.
- Equate “we implemented Wong MHL” with “Moffitt is our partner.”

---

## Next meetings (priority)

1. AdventHealth Winter Haven / Orlando TRI — clinical access, Polk equity narrative.  
2. UF BioCore — HPC allocation + FCIF eligibility.  
3. Sylvester epigenetics / Precision Medicine — cfDNA + pangenome scientific home.  

One of **UF or Sylvester** replaces Moffitt as named lead on the external brief.
