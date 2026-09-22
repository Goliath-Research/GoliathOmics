NSF SBIR Phase I is the right federal instrument **if Goliath is selling a product or service**, not if the only story is “the code is free for Florida universities.” It sits next to PESOSE and FCIF; it does not replace them.

Current vehicle: **NSF 26-510** (America’s Seed Fund), plus a narrower instrumentation pilot **NSF 26-511**. NSF takes no equity. The company keeps IP.

---

## The Phase I box

| Item | NSF 26-510 |
| :--- | :--- |
| Amount | Up to **$305,000** all-in (direct, indirect, fee, TABA, optional I-Corps) |
| Duration | **6–18 months** |
| Purpose | Feasibility / proof-of-concept on a **high-risk technical** question |
| Who submits | U.S. for-profit **small business concern** only (≤500 staff including affiliates) |
| Ownership | Majority U.S. citizens or permanent residents. Majority VC / PE / hedge-fund ownership is a common disqualifier |
| Cost share | None on the base award |
| Next full-proposal dates | **4 Nov 2026**, then **4 Mar 2027**, then **7 Jul 2027** (5 p.m. local) |
| Gate | Mandatory **Project Pitch** + email invitation before any full proposal |

July 27, 2026 already closed. The operational date is November 4 — but only if a pitch is invited first. Pitch review is typically **1–2 months**. A company may submit **two pitches per 12 months**, one at a time; after an invitation you wait until that full proposal is resolved. Invitations cover the next two submission deadlines.

Registrations needed before a full proposal: SAM.gov, SBA Company Registry, Research.gov. Start SAM now; it is often the long pole.

Downstream (not Phase I, but why Phase I exists): Phase II up to **$1.25M / ~24 months**; Fast-Track up to **~$1.56M** if you already have enough data to skip a standalone Phase I; later Strategic Breakthrough is PO-invited and not relevant yet. Combined Phase I+II is the “up to ~$2M” figure NSF advertises.

---

## SBIR vs STTR

| | **SBIR** | **STTR** |
| :--- | :--- | :--- |
| Lead | Goliath | Goliath, with a **required** U.S. research institution |
| Work split | Company does most of the R&D | Company ≥40%, institution ≥30% |
| When to use | Tech and IP live inside the company | You want UF / Florida Poly / Sylvester as a formal co-developer (GPU benchmark, methods concordance) |
| IP | Company-owned; institution agreement recommended if there is a subaward | Executed IP allocation required before award |

For your map: **SBIR** if `mojo-align` / GoliathWorkflow is Goliath IP and UF only provides HPC letters. **STTR** if a UF or Florida Poly faculty co-PI will write kernels, evaluation harnesses, or the certificate reference implementation. Do not use STTR just to “look academic.”

---

## What NSF will fund here (and what it will bounce)

NSF does not publish a cancer-informatics RFP. You pick a topic area and argue technical risk + commercial potential. Closest bins:

- **BT7** Life science research tools (genomics / computational biology tools)  
- **CH1–CH3** Cloud / HPC algorithms, architecture, AI+HPC  
- **BM1** Diagnostics (non-clinical feasibility only)  
- **DH2 / DH4** AI in healthcare; diagnostics software  
- **NSF 26-511** scientific instrumentation — plausible if you frame GPU bisulfite/pangenome alignment as an instrument, not a hospital product  

**Explicit NSF exclusions in biomedical/digital-health language:** clinical trials, clinical efficacy/safety studies, and work done *primarily* for regulatory clearance. A Phase I that is “lock a 510(k) dataset and write the FDA package” is the wrong agency. Feasibility of a novel aligner, mate-clip extractor, or CAAS-backed multiomics runtime is in scope. Pivotal SaMD is not.

NSF also rejects incremental software (“we wrapped Bismark in a nicer UI”). The pitch must name a **technical risk that can fail**: dual-graph C2T∥G2A correctness vs linear bwa-meth; ROCm+CUDA portability without two codebases; overlap-clip changing downstream MHL; CAAS cache invalidation under real cohort churn.

Open source is allowed. NSF even points commercializers of open tools to SBIR rather than PESOSE. What they will not fund is “keep the repo alive.” They fund a **saleable unit**: supported runtime, BYOC appliance, validation-as-a-service, locked procedure packs, or a commercial license around an open core.

---

## Phase I technical aims that fit Goliath (pick one spine)

Do not submit “the whole platform.” Phase I is one bet.

**Option A — HPC instrument (strongest NSF fit)**  
Hypothesis: native-Mojo `fq2bam-meth` + `MojoGiraffe` matches or beats Clara Parabricks on H100/MI300X wall-clock and removes reference bias on a diverse Florida-like pangenome panel, with automatic HBM tiling so jobs do not OOM.

Milestones: published benchmark harness; ≥60% wall-clock claim tested on 20–50 WGBS/EM-Seq samples; CUDA and one ROCm path; error modes documented.

Topic: CH or BT7. Optional 26-511 if you sell it as instrumentation.

**Option B — Artifact-free methylation product**  
Hypothesis: coordinate mate-clip + 12-byte Zstd HDF5 changes coverage and ratios enough to move a locked FeatureCuts or MHL call, versus MethylDackel/bedGraph.

Milestone: paired-end overlap ablation on public + partner de-identified BAMs; storage and I/O vs bedGraph; QC manifest contract.

**Option C — Reproducible BYOC runtime (commercial wedge)**  
Hypothesis: CAAS + four-layer config can rerun a locked procedure pack on a customer Slurm partition with bit-stable action hashes and no vendor database.

Milestone: one-click worker image; lease recovery drill; cost model vs a commercial cloud tenancy.

Avoid as Phase I: “train a new prostate classifier,” “FDA 510(k),” “statewide community access.” Those belong on FCIF / ITCR / later NIH SBIR.

---

## Commercial story NSF expects

Reviewers score **Intellectual Merit, Broader Impacts, and Commercial Potential**. The market section of the pitch is only 1,750 characters; it still has to name a buyer.

A coherent open-core model:

| Who pays | For what | Not for |
| :--- | :--- | :--- |
| Academic / community hospital | $0 seats on OSI core (FCIF talking point stays true) | Nothing |
| Cancer-center core / CRO / well-resourced lab | Annual support, validated worker images, pack lock, deployment | Relicensing PostgreSQL |
| Future diagnostic lab | Commercial license + SaMD evidence packaging | Using Phase I as the clinical trial |

If the only revenue line is “grants forever,” NSF will call it a research lab, not a small business. Letters that help: a core director who would buy support; a CRO who would pay for a locked pack; not a dean who wants free software.

---

## Project Pitch (do this before writing 15 pages)

Four fields:

1. **Technology innovation** (3,500 chars) — the risk, not the architecture diagram.  
2. **Technical objectives and challenges** (3,500) — what Phase I will measure.  
3. **Market opportunity** (1,750) — who pays, why now, why not Parabricks / Bismark / closed SaaS.  
4. **Company and team** (1,750) — Winter Haven company, who writes Mojo/C, who has shipped software.

To hit **4 Nov 2026**, the pitch needs to go in **this month**. A mid-October invitation is already tight. Safer planning target if the pitch slips: **4 Mar 2027** (same week as PESOSE Track 1). That is workable: pitch in November–December, write the full proposal in January–February.

Do not burn both annual pitches on two flavors of the same platform. One NSF spine (A, B, or C). Save the second pitch only if the first is declined on topic fit.

---

## How Phase I coexists with the rest of the stack

| Instrument | Pays | Does not pay |
| :--- | :--- | :--- |
| **NSF SBIR I** | Company R&D on one technical risk; prototype; customer discovery | Ecosystem governance, statewide sequencing, FDA pivotal |
| **PESOSE Track 1** | Docs, governance, contributor onboarding, security plan | Product features, salaries to finish kernels |
| **FCIF** | Florida cohort, HPC queue, AdventHealth access | Company productization |
| **NCI ITCR U01** | Academic dissemination of cancer informatics | Company commercialization |
| **NIH/NCI SBIR** | Cancer-specific diagnostic/software feasibility; closer to intended use | NSF-style deep-tech / HPC framing |

Same dollar of engineer time cannot be charged to two awards. Split by **work package**: SBIR = aligner/runtime risk; PESOSE = community; FCIF = samples and host GPUs.

**NIH/NCI SBIR is the sister option** if the buyer is oncology, not NSF’s “deep tech” reviewer. NCI grant receipt dates run **5 Sep / 5 Jan / 5 Apr**; Phase I is typically ~$300k; NCI also runs contracts and a first-time-applicant STEP program (next STEP deadline 12 Oct 2026) for shops that have never had NIH SBIR. If the November NSF pitch is declined as “too clinical,” pivot the same technical aims to the **5 Jan 2027** NIH cycle rather than rewriting for PESOSE.

---

## Eligibility and process checklist

1. Confirm Goliath Research Inc. is a for-profit SBC, ≤500 staff, majority U.S.-owned, not majority VC-controlled.  
2. Start SAM.gov + SBA registry + Research.gov this week.  
3. Choose SBIR vs STTR (STTR only with a named UF/Florida Poly SOW and IP term sheet).  
4. Pick **one** Phase I hypothesis (A, B, or C above).  
5. Submit the Project Pitch in the current window.  
6. If invited, aim for 4 Nov 2026 only if the invitation arrives with ≥3 weeks of writing time; otherwise 4 Mar 2027.  
7. Budget: PI + 1–2 engineers + cloud/HPC for the benchmark (not AdventHealth sequencing) + TABA + I-Corps. Keep university subaward modest on SBIR; use STTR if the university must do ≥30%.  
8. Keep the OSI core. SBIR pays for the **productized path** (support, images, commercial license), not for closing the repo.

---

## What would make a Phase I fail

- Pitch reads like the partnership brief (community access, $0 seats, Polk equity) with no buyer.  
- Aims are “deploy GoliathOmics on HiPerGator.” That is a grant deliverable, not an NSF technical risk.  
- Clinical-performance claims or FDA writing as the Phase I work.  
- Fast-Track without published benchmarks — you do not have Phase-II-ready data yet.  
- Two federal proposals that copy-paste the same specific aims.

**Bottom line:** NSF SBIR Phase I is viable as a **$305k, 6–18 month company award** on GPU alignment or artifact-free extraction, with a commercial support/license wedge. Submit the Project Pitch now if you want a chance at 4 Nov 2026; otherwise treat 4 Mar 2027 as the coordinated NSF week (SBIR full proposal + PESOSE Track 1) after the public repo and HPC pilot exist. Use NIH/NCI SBIR if reviewers tell you the work is cancer-product, not deep-tech infrastructure.