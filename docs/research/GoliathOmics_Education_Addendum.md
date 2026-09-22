# Education Addendum  
## Florida Community-Access Partnership Brief

**Presenting organization:** Goliath Research Inc. (Winter Haven, FL)  
**Platform:** GoliathOmics (GoliathWorkflow · mojo-align · MethylExtractor)  
**Parent brief date:** September 15, 2026  
**Addendum date:** September 16, 2026  
**Status:** Companion to the statewide community-access / joint-grant strategy — not a substitute for the UF or Sylvester HPC lead

---

### Purpose

This addendum turns the parent brief’s “community benefit” and “zero seat licenses” claims into a credit-bearing workforce path: a **graduate certificate / concentration in Genomic Software Engineering & Regulatory Multiomics**, delivered inside existing Florida programs.

Education is a **distribution, talent, and modest-services channel**. It does not replace:

- UF Health Cancer Center or Sylvester as FCIF-eligible lead and HPC host  
- AdventHealth Winter Haven / Orlando TRI as the Polk–Central Florida clinical arm  
- Goliath as technology co-investigator  

It also does **not** create a new standalone master’s in year one. Florida already has the degree shells. The missing object is a locked practicum on an auditable, open toolchain.

---

### 1. Policy that must sit in front of any syllabus

| Rule | Implication |
| :--- | :--- |
| Core engine is OSI-open (MIT-class, public repo) | Eligible for NCI ITCR software-sharing plans, NSF POSE/PESOSE ecosystems, and future Open Source for Science cycles |
| Academic, teaching, and community-hospital use | No software seats; grant budgets list **$0 licenses** |
| Partners own clinical panels and discoveries | Goliath retains core software IP |
| Commercial production / CRO / closed tenancy | Separate commercial license or paid support — not part of the certificate |
| Distinct intended uses in student work | Gatekeeper NPV (`cfdna_emseq_targeted` / FeatureCuts) and mCRPC MHL+OS (`cfdna_emseq_mhl_survival`) are never scored as one study |

A “non-profit-only” license on the core would block the same philanthropic and ITCR mechanisms this partnership needs. Keep the science open; monetize deployment, SaMD packaging, locked procedure packs, and training.

---

### 2. Host map (one grant lead, one education affiliate)

Do not ask four universities to co-lead. Name **one** cancer-institute PI and **one** education co-site in the LOI.

| Institution | Existing shell | Role in this addendum | Fit |
| :--- | :--- | :--- | :--- |
| **UF Health Cancer Center + CISE / HOBI** | MS Medical Sciences — Biomedical Informatics; CISE bioinformatics; HiPerGator | **Preferred FCIF / ITCR academic home.** Certificate as BMI or CISE special-topics + practicum on HiPerGator | NCI-designated cancer institute, GPU already paid, IRB and science cores |
| **Sylvester / UM Frost IDSC** | MS Data Science — bioinformatics track; Pegasus; Onco-Genomics SR; SPARK | Alternate academic home. Same practicum on Pegasus; South Florida diversity / pangenome story | Cancer institute + dedicated Sylvester HPC partition |
| **UCF** | MS Data Analytics; MS Biotechnology (informatics electives) | Education affiliate and Orlando workforce node; AdventHealth TRI geography | Fastest path to working analysts in the clinical catchment |
| **Florida Polytechnic University (Lakeland)** | BS / MS Data Science; MS Computer Science | **Preferred year-one practicum host** (≈20 minutes from Winter Haven). Capstones, tests, docs, GPU worker images | Engineering culture, no competing enterprise cancer stack, cannot lead FCIF |

**USF** (MS Bioinformatics & Computational Biology) may consume the open toolchain and send students to shared workshops. It is not proposed as lead: Tampa is already served by Moffitt’s commercial fabric, which this partnership is designed not to displace.

**Recommended LOI naming (Track A):**  
UF *or* Sylvester = corresponding PI / HPC host.  
Goliath = technology co-I.  
AdventHealth = clinical / equity site.  
Florida Poly *or* UCF = education affiliate (subaward or unfunded collaborator).

---

### 3. The credit-bearing object (year one ≠ a new degree)

**Year-one object:** 3-credit special topics / directed research that can be approved inside an existing catalog this academic year.

**Year-two object:** 12–15 credit **Graduate Certificate in Genomic Software Engineering & Regulatory Multiomics** (name may be localized by the registrar).

Stackable later into UF BMI, UM Data Science, UCF MSDA, or Florida Poly MS Data Science. A full master’s is a 2028 catalog action and is out of scope for FCIF’s 12-month clock.

#### Certificate shape (15 credits, example)

| Module | Credits | Taught against |
| :--- | ---: | :--- |
| A. Open workflow engines & configuration hierarchy | 3 | `GoliathWorkflow` on PostgreSQL (`cfg` / `wf`), DomainProgram IR, CAAS |
| B. GPU alignment & pangenome bias | 3 | `mojo-align` (linear bwa-meth path + dual-graph C2T ∥ G2A); HiPerGator or Pegasus workers |
| C. Methylation artifacts & haplotype methods | 3 | `MethylExtractor` mate-clip; MHB/MHL vs mean-methylation FeatureCuts |
| D. SaMD, partitions, and claim gates | 3 | `samd_research` → `samd_holdout_enrichment`; patient-disjoint keys; Wilson LCBs |
| E. Practicum / capstone | 3 | De-identified AdventHealth or academic-core cohort; locked procedure pack; reproducibility notebook |

Modules A–D can be offered as special topics while the certificate is in governance. Module E is the only piece that requires a DUA/IRB from the clinical or HPC host.

---

### 4. Learning outcomes (what a graduate can do that a generic bioinformatics MS cannot)

On completion, the student can:

1. Compile and run a DomainProgram on institutional HPC without a commercial orchestrator.  
2. Explain and measure reference bias on a diverse Florida cohort; choose linear vs pangenome alignment and report mapping dropouts.  
3. Demonstrate that coordinate-based overlapping-mate clipping changes coverage and methylation ratios versus an unclipped extractor.  
4. Execute `cfdna_emseq_targeted` (FeatureCuts / gatekeeper NPV) and `cfdna_emseq_mhl_survival` (Wong/Wang 2026 method class: MHB, MHL, Cox / KM / time-AUC) as **separate** intended uses.  
5. Enforce patient-disjoint partitions and show that a claim gate blocks clinical language under `samd_research`.  
6. Produce an immutable `.action_results` manifest (input hashes, tool revisions, hardware flags, seeds).  
7. Write a budget justification in which software licenses are **$0** and compute is queue time on a host already paid for.

These outcomes are the education translation of parent-brief sections 2–6. They are also the workforce argument for FCIF “novel technologies for diagnosis” and ITCR dissemination.

---

### 5. How the course uses the product (not a wrapper around Bismark)

| Pack layer | Student-facing artifact |
| :--- | :--- |
| Process packs | Methylation, RNA-Seq, optional proteomics seam — typed QC contracts |
| Assay procedure packs | `cfdna_wgbs_plasma`, `buffy_wgbs_pangenome_gene_fc`, `cfdna_emseq_targeted`, `cfdna_emseq_mhl_survival` |
| Application overlays | Prostate gatekeeper **or** mCRPC OS — never both scored as one project |
| Backend | Single PostgreSQL; CAAS cache reuse when only the classifier changes |
| Compute | Slurm-friendly workers on HiPerGator, Pegasus, or a hospital/education GPU — no new OCI tenancy |

Optional concordance lab (Florida PI, public methods only): native MHL/Cox versus mHapSuite+R on a shared subset. Not a Moffitt IT integration.

---

### 6. Governance, labor, and IP

```text
┌──────────────────────────────┬──────────────────────────────┬──────────────────────────┐
│ Academic host (UF / Sylvester│ Goliath Research             │ Education affiliate      │
│ + clinical site)             │                              │ (Florida Poly or UCF)    │
├──────────────────────────────┼──────────────────────────────┼──────────────────────────┤
│ IRB / DUA home               │ Curriculum co-design         │ Instructor of record or  │
│ HPC allocation               │ Runtime bundle & packs       │ capstone coordinator     │
│ Residual / de-identified     │ SaMD evidence packaging      │ Student CI, tests, docs  │
│ samples                      │ Technology co-I on grants    │ Local Winter Haven /     │
│ Cancer-institute PI          │                              │ Orlando pipeline         │
└──────────────────────────────┴──────────────────────────────┴──────────────────────────┘
```

- Students are not a shadow workforce for PHI. Year-one practicum uses de-identified or public/published sets until the DUA exists.  
- Course materials that document the open toolchain are released under the same OSI license as the code.  
- Student project IP on *panels and clinical findings* follows the host institution’s policy; Goliath does not claim those discoveries.  
- Goliath staff may serve as adjunct / co-instructor; salary is a grant personnel line, not a seat fee.

---

### 7. Budget language (copy into FCIF / ITCR justifications)

**Software licenses: $0.** PostgreSQL, Python, Mojo, C/HTSlib, TypeScript. No database seats, no commercial aligner licenses, no new cloud AI tenancy.

#### A. Florida Cancer Innovation Fund (12-month project; up to $2,000,000)

Use education as a **specific aim or measurable deliverable**, not the proposal title.

Suggested aim language:

> Deploy GoliathOmics on [HiPerGator / Pegasus]; process a Polk / Central Florida retrospective cfDNA or WGBS set with AdventHealth; establish locked research metrics with Wilson CIs; stand up a 3-credit practicum and draft a 15-credit certificate so community and academic analysts can run the same open packs. Software licensing cost: $0.

Illustrative education lines (scale to the chosen award size; these are not a full $2M budget):

| Line | Role | Notes |
| :--- | :--- | :--- |
| Education affiliate instructor or Goliath adjunct (0.2–0.4 FTE) | Personnel | Course + practicum, not product sales |
| 4–8 student practicum stipends or hourly HPC helpers | Personnel / other | Tests, docs, wall-clock benchmarks vs nf-core/Bismark |
| HPC queue time (education partition or project allocation) | Compute | Host institution rates; BYOC |
| Workshop (1–2 days) for AdventHealth / community analysts | Other | Travel in-state only |
| Commercial software / database seats | — | **$0** |

Official FY 2026–27 rolling close dates to verify on the Department portal before submission: Period 1 — 25 September 2026; Period 2 — 6 November 2026; Period 3 — 18 December 2026. The parent brief’s “Period 2: October 22, 2026” should be reconciled to the live FOA.

#### B. NCI ITCR (parallel)

| Mechanism | Why this addendum belongs |
| :--- | :--- |
| U01 early-stage (RFA-CA-27-019; up to $300,000 DC/year, 3 years) | Initial dissemination of an open cancer-informatics platform + training materials |
| U24 enhancement (RFA-CA-27-020; up to $600,000 DC/year) | If a UF/Sylvester pilot already shows users beyond Goliath |

ITCR applications must include a software-sharing plan naming an OSI-approved license. The certificate and public docs are the dissemination plan, not a separate product.

#### C. NSF POSE / PESOSE (ecosystem, not a cancer trial)

Track 1 planning (~$300,000) funds governance, onboarding, security, and training for an open-source ecosystem around an already-released product. That is the federal home for “certificate + contributor community,” distinct from FCIF’s 12-month clinical-access clock.

#### D. What education does *not* fund

Salaries for core engine development should remain on research/technology personnel lines (FCIF co-I, ITCR, Bankhead-Coley, SBIR). Tuition is not the P&L. Between grants, paid work is deployment, pack lock, validation reports, and instruction—not seats.

---

### 8. Twelve-month education deliverables (aligned to parent-brief §9)

| Quarter | Deliverable | Owner |
| :--- | :--- | :--- |
| Q1 (this month → FCIF close) | LOI names education affiliate; 3-credit special-topics paperwork started; DUA path identified | Academic PI + Goliath + affiliate |
| Q1 | 20–50 sample HPC pilot used as the first lab notebook template | HPC host + Goliath |
| Q2 | First special-topics section enrolled (or directed-research equivalents) | Education affiliate |
| Q2–Q3 | Public course repository: lab guides, fixture data, CI that builds workers | Goliath + students |
| Q3 | Certificate proposal in host governance | Education affiliate + academic PI |
| Q4 | N analysts trained; locked research metrics from the Polk/academic set; $0 license attestation in the final report | All parties |

Success is not “a master’s approved.” Success is **students and community analysts running the same packs the grant used**, on the host’s GPUs, with audit trails a future SaMD package can cite.

---

### 9. Immediate checklist (education track only)

1. Confirm OSI license text on the public core before any dean meeting.  
2. Choose **UF or Sylvester** as grant lead; choose **Florida Poly or UCF** as year-one classroom.  
3. One-page MOU rider: practicum, DUA, $0 seats, IP split as in parent brief §8.  
4. Reconcile FCIF Period dates to the live FOA; if Period 1 (25 Sep 2026) is still feasible with a lead PI, do not wait for a catalog change.  
5. Offer the HPC wall-clock pilot as the first assignment, not as a software demo.

---

**Contact (unchanged)**  
Goliath Research Inc. · Winter Haven, Florida  
partnerships@goliathresearch.com · goliathresearch.com  
Platform: GoliathOmics (GoliathWorkflow [PostgreSQL], MethylExtractor, mojo-align)

*This addendum does not designate Moffitt Innovation as education or IT lead. Optional MHL concordance with a Florida PI remains science-only.*