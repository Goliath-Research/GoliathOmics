The university-program idea is useful, but it will not by itself pay for salaries, GPUs, or samples. Treat education as a **distribution and talent channel** sitting next to the partnership model already in your brief—not as a replacement for it. A new master’s takes 18–36 months in the Florida SUS (curriculum committees, SACSCOC, Board of Governors). You need cash and users in the next two grant cycles.

Below is a strategy that keeps GoliathOmics free for non-profits, funds continued development, and uses universities without competing with Moffitt’s enterprise stack.

---

## 1. Fix the license before you recruit deans

“Open source for non-profit use” is **not** the same as OSI-approved open source. That distinction decides which money you can take.

- Philanthropic software funds (Open Source for Science / former CZI EOSS) generally **reject** “non-commercial only” licenses. They want a public repo and an OSI license.
- NCI ITCR also expects an open-source sharing plan and prefers OSI-approved licenses.
- NSF POSE / PESOSE funds **ecosystems** around already-open products; for-profits can apply, but the product itself must be openly licensed.

A workable split:

| Layer | License / access | Who pays |
|---|---|---|
| Core engine (`GoliathWorkflow`, `mojo-align`, `MethylExtractor`, packs) | OSI (MIT already matches your C/Mojo forks) | Grants + your own payroll |
| Research / teaching / community-hospital use | Free, no seats | $0 software line in FCIF/NIH budgets |
| Commercial clinical production, CRO, SaaS tenancy, closed SaaS | Separate commercial license or paid support | Industry |
| What you sell instead of seats | Deployment on *their* HPC, SaMD evidence packaging, training, locked procedure packs, validation studies | Grants + services |

Do **not** put the science behind a non-commercial clause if you want ITCR, POSE, or OS4Science. Keep IP as in the brief: partners own panels and clinical discoveries; Goliath keeps core software IP and can dual-license later.

---

## 2. Do not invent a standalone “genomics software engineering” master’s

Florida already has the pieces. You want a **named concentration + practicum on GoliathOmics**, not a new degree.

Natural homes:

- **UF** — MS Biomedical Informatics; CISE bioinformatics; HiPerGator; UF Health Cancer Center. This is still the strongest **grant-eligible lead**.
- **University of Miami / Sylvester** — MS Data Science bioinformatics track; Pegasus; Onco-Genomics SR; SPARK undergrad cancer pipeline. Strong South Florida diversity story.
- **UCF** — MS Data Analytics + Biotech MS informatics electives; Orlando / AdventHealth TRI geography.
- **Florida Poly (Lakeland)** — BS/MS Data Science, ~20 minutes from Winter Haven. No cancer center, so they cannot lead FCIF, but they are the fastest place to stand up a software-engineering practicum and capstone.
- **USF** — dedicated MS Bioinformatics & Computational Biology. Scientifically relevant, but it sits in Moffitt’s backyard; use as a consumer of the open toolchain, not as the political lead.

Ask each school for the **smallest credit-bearing object** they can approve this academic year: a 3-credit special topics / capstone, then a 12–15 credit graduate certificate. That is how you get students on the code before a catalog change.

What the curriculum should actually teach (this is the differentiator vs generic bioinformatics):

1. Workflow engineering on PostgreSQL + CAAS (reproducibility, leases, audit)
2. GPU alignment and pangenome bias (`mojo-align`)
3. Artifact-aware methylation extraction (mate clipping, MHB/MHL)
4. Patient-disjoint splits and SaMD claim gates
5. Packs as product architecture (process / procedure / application)

That is software engineering *for regulated multiomics*, not another STAR + DESeq2 course.

---

## 3. Keep a three-institution map (do not collapse it into “a university”)

Your brief is still the right political geometry. Education is a fourth spoke, not a new lead.

| Role | Who | Why |
|---|---|---|
| Grant-eligible cancer-institute PI + HPC | UF Health Cancer Center (HiPerGator) **or** Sylvester (Pegasus) | FCIF and most NCI mechanisms want a licensed Florida cancer institute |
| Technology co-I | Goliath (Winter Haven) | Platform, SaMD packaging, open toolchain |
| Clinical / equity arm | AdventHealth Winter Haven → Orlando TRI | Polk / Heartland access, residual plasma |
| Workforce / SE pipeline | Florida Poly + UCF (and UF/UM students on HPC) | Local talent, capstones, documentation, tests |
| Explicit non-lead | Moffitt OCI–NVIDIA–Deloitte fabric | Do not ask them to adopt your stack; optional science-only concordance on published MHL methods |

Community benefit stays the same: grant dollars buy sequencing and care, not seats; PHI stays inside the host firewall.

---

## 4. Stack funding so education is a line item, not the business

**Near term (this quarter)**  
Florida Cancer Innovation Fund FY 2026–27: ~$70M statewide, up to $2M, 12 months. Official rolling deadlines are **Period 1: 25 Sep 2026**, **Period 2: 6 Nov 2026**, **Period 3: 18 Dec 2026**. Your brief listed 22 Oct for Period 2—verify against the FOA before you lock a calendar. Period 1 is still open as of today.

Put education in the aims as a deliverable, not the title: *deploy on HiPerGator/Pegasus; process a Polk cfDNA/WGBS set; locked research metrics; $0 software licenses; train N students/analysts on the open packs.*

**Software-specific federal / philanthropic (parallel, not instead)**  
- NCI ITCR: U01 early-stage (~$300k DC/year, 3 years) or U24 enhancement (~$600k DC/year). Natural fit for an open cancer-informatics platform hosted at UF or Sylvester. Deadlines in this cycle include 19 Oct 2026.
- NSF POSE / PESOSE: Phase/Track 1 planning ~$300k; Track 2 ecosystem ~$1.5M. This funds *community, governance, training, security*—exactly what a university partnership needs. Next windows include 1 Sep annually and 2 Mar 2027.
- Open Source for Science Fund (life-sciences tracks up to $250k / $1M over 2 years)—only if the license is truly open. The 2026 LOI window already closed; watch the next cycle.
- Bankhead-Coley and NIH academic–industrial partnerships remain as in the brief; academic lead must be UF or Sylvester.

**What actually pays salaries between grants**  
Do not sell seats to hospitals. Sell work that universities and cancer centers already budget:

- Slurm/BYOC deployment and worker images  
- Procedure-pack lock + validation report  
- Instructor-of-record / adjunct for the certificate  
- Sponsored capstones (student labor against real AdventHealth/UF de-identified sets)  
- Optional commercial license later for CROs and for-profit labs  

That is how Galaxy, nf-core-adjacent shops, and many Bioconductor cores survive: grants + services + training, not tuition as the P&L.

---

## 5. A 12-month sequence that is actually executable

**Weeks 1–4 (before FCIF Period 1/2)**  
1. Confirm OSI license on the public core; write a one-page “academic vs commercial” policy.  
2. Pick **one** cancer-institute lead (UF *or* Sylvester) and one education co-site (Florida Poly or UCF). Two full academic leads will stall the LOI.  
3. Offer a 20–50 sample HiPerGator or Pegasus pilot (wall-clock vs nf-core/Bismark) as in Track A of the brief.  
4. Draft an MOU that names: academic PI, Goliath co-I, AdventHealth clinical site, education affiliate. Do not name Moffitt Innovation as lead.

**Fall 2026**  
5. Submit FCIF with a training aim (certificate design + N analysts trained).  
6. Stand up a 3-credit special topics course at Florida Poly or UCF: students compile packs, run CAAS, write tests, produce a reproducibility notebook.  
7. Start an ITCR U01 or NSF POSE Track 1 outline with the same academic PI.

**Spring–summer 2027**  
8. Convert the special topics course into a 12–15 credit certificate (Genomic Software Engineering / Regulatory Multiomics).  
9. Use certificate students as the documentation and CI workforce for the open repo.  
10. If FCIF hits, Bankhead-Coley or ITCR becomes the 36-month sustainment layer.

**Do not wait for the master’s to exist before the grant.** The grant creates the lab section; the certificate is the course catalog catching up.

---

## 6. How the education channel serves the original goals

- **Non-profit access:** students, community hospitals, and academic PIs run the same MIT/PostgreSQL stack on institutional HPC.  
- **Development funding:** grants pay engineers; courses produce contributors and testers; services pay the gap.  
- **Why Moffitt was the wrong sole partner:** they already bought fabric. A university needs *curriculum + reproducibility + cheap compute*, which is exactly what you have.  
- **Polk / Heartland story:** Winter Haven + Florida Poly + AdventHealth is a geography no Tampa or Miami stack covers well. Keep that in every aim.

---

## 7. Risks to name internally

- A restrictive non-commercial license will close OS4Science, weaken ITCR, and still not produce revenue.  
- A new master’s is a 2028 product; FCIF is a 12-month clock.  
- Students on real cfDNA need IRB/DUA from AdventHealth or the academic lead—start that paperwork with the MOU.  
- Do not blur gatekeeper NPV (FeatureCuts) and mCRPC MHL+OS packs in student projects; your brief already treats those as distinct intended uses.

If you want a next artifact, the highest-leverage one is a two-page **education addendum** to the existing brief: proposed certificate learning outcomes, host options (UF / UM / UCF / Florida Poly), and the exact FCIF/ITCR budget lines for “training and open-toolchain dissemination” with $0 seats.