**POSE still exists as a strategy, but the live solicitation is no longer named POSE.** NSF 24-606 is archived. The current program is **PESOSE — Pathways to Enable Secure Open-Source Ecosystems (NSF 26-506)**. It keeps the old two-phase structure, adds a security track, and is run by NSF TIP.

That matters for Goliath: this is money for a **managing organization and community around an already-public product**, not money to finish `mojo-align`, buy samples, or run a cancer study.

---

## What it is (and is not)

PESOSE funds the translation of an existing open-source research product into a durable ecosystem: governance, distributed contributors, user onboarding, CI/CD, supply-chain hygiene, and security/privacy. Healthcare is an explicit application domain.

It does **not** fund:

- Building or extending the core product (new packs, new kernels, new classifiers)
- Paying the original developers to keep writing science code as the main activity
- Well-resourced ecosystems that already have a foundation and paid staff
- Products that are not available for open use
- Commercialization of a closed product (that is NSF SBIR/STTR)

NSF’s own FAQ is blunt: *“POSE program funding is intended to establish, enhance, and grow ecosystems in which the core products are presently and will remain licensed as open source.”* If the real goal is a paid clinical product, use SBIR in parallel. PESOSE and SBIR can coexist only if the **core stays OSI-open**.

That is why the education addendum and the license split are prerequisites, not extras.

---

## Current money and calendar

| Track | Old POSE name | Ceiling | Duration | ~Awards | What it buys |
| :--- | :--- | ---: | :--- | ---: | :--- |
| **1** Scope and plan | Phase I | $300,000 | ≤ 1 year | ~30 | Discovery, governance design, licensing, security plan, community experiments, training model |
| **2** Establish and expand | Phase II | $1,500,000 | ≤ 2 years | ~10 | Managing org, CI/CD, contributor pipeline, sustainability metrics |
| **3** Secure an existing OSE | New (absorbs Safe-OSE) | $1,500,000 | ≤ 2 years | ~10 | Vulnerabilities, SBOMs, IAM, supply chain, socio-technical risk |

Program envelope is up to **$40 million**, 40–60 awards. No letter of intent. No cost share (voluntary committed cost share is prohibited).

**Deadlines (5 p.m. submitting organization’s local time):**

- 1 September 2026 — **already passed** as of today
- **2 March 2027** — first real window for Goliath
- First Tuesday in September and March thereafter

Track 1 awardees must do NSF’s “I-Corps for PESOSE” experiential activity. Track 2 can skip it if they already completed it in Track 1.

---

## Who can submit

Eligible leads include U.S. universities, nonprofits, **U.S.-based for-profits (including small businesses)**, state/local government, and tribal nations. A company can lead without a university. For-profits must be U.S.-owned and controlled (>50% equity, fully diluted, by U.S. citizens/permanent residents or qualifying U.S. firms). The PI at a company must be an employee normally resident in the U.S.

Multi-organization teams: one lead plus subawards. That maps cleanly onto Goliath (lead or co-I) + UF or Florida Poly (education / OSPO) + letters from AdventHealth or a cancer-center user group.

A 501(c) foundation is **not** required for Track 1. Track 2 must say what legal form the managing organization will take.

---

## Readiness bar (this is the hard part)

The product must **already exist in public as open source**, with a pointer in the proposal (citation/reference; NSF does not want raw URLs in the project description). Track 1 wants **external users** and 3–5 letters from third parties *not* on the team. Track 2 also wants **external contributors**, not just downloaders.

Honest assessment for GoliathOmics today:

| Requirement | Likely status | What to do before 2 Mar 2027 |
| :--- | :--- | :--- |
| Public OSI-licensed core | Claimed MIT on `mojo-align` / `MethylExtractor`; platform must match | Publish repos, LICENSE, CONTRIBUTING, citation file |
| External users | Partnership brief is still prospective | Get 3–5 letters: UF/Sylvester analyst, Florida Poly instructor, AdventHealth informatics, one outside-Florida lab |
| External contributors | Probably not yet | Student PRs and pack recipes from the 3-credit practicum count |
| Not “feature development” | Risk if aims read like a product roadmap | Aims = governance, docs, CI, training, security, contributor onboarding |
| Core remains open | Dual-license *services* are fine; locking the engine is not | Write the license policy now |

If those letters and a public repo are not real by January 2027, do not force Track 2. Track 1 is the correct first award.

Track 3 is premature. You are not yet an “existing OSE” with a dependency graph NSF would fund to harden. After Track 1/2, PHI, BAM supply chain, and worker-image signing become a Track 3 story (HDF5 already won a related Safe-OSE award; that is the neighborhood).

---

## How this sits next to FCIF, ITCR, and the certificate

Do not put the same work in two proposals.

| Mechanism | Pays for | Does not pay for |
| :--- | :--- | :--- |
| **FCIF** | Sequencing, Polk cohort, HPC queue, 12-month clinical-access pilot | Ecosystem governance |
| **NCI ITCR** | Cancer-informatics tool development and dissemination | A generic managing org with no cancer specific aims |
| **PESOSE Track 1** | Certificate design, contributor pathway, license/governance, security plan, workshops | Wet-lab samples, new alignment features, SaMD pivotal trial |
| **NSF SBIR** | Commercial validation of a service around the open core | Keeping the core closed |

The education addendum is almost a Track 1 work plan: onboarding materials, practicum, CI that students run, workshops for community-hospital analysts. NSF even lists “provides training and on-boarding to new developers and users” as a canonical OSE function.

Comparable funded projects (old POSE, useful models): Rosetta scoping, PlantCV community, iCn3D education ecosystem, Gen3 data commons Phase II, GRASS GIS expansion. Those abstracts talk about governance and contributors, not a new algorithm.

---

## What a Goliath Track 1 proposal should look like

**Title pattern:** `PESOSE: Track 1 Scoping an open-toolchain ecosystem for regulatory multiomics`

**Lead options**

1. **Goliath as lead** — allowed; strongest if you can show U.S. ownership and a PI employee in Florida. University subawards for education and HPC letters.  
2. **Florida Poly or UF as lead** — easier NSF hygiene and an Open Source Program Office narrative; Goliath as subaward for product stewardship. Better if company ownership docs are messy.

**Seven-page story (Track 1 limit)**

1. Product already public: GoliathWorkflow + mojo-align + MethylExtractor; PostgreSQL; packs.  
2. National need: academic cancer informatics locked into proprietary clouds; grant dollars diverted to seats; reference bias in diverse populations; auditability for SaMD.  
3. Ecosystem discovery: who uses linear vs pangenome WGBS; community hospitals vs NCI centers.  
4. Governance options: company-stewarded open core vs university OSPO vs future foundation.  
5. Licensing: OSI on the engine; paid deployment/SaMD packaging only.  
6. Security/privacy plan: no PHI in public CI; signed worker images; SBOM for HTSlib/HDF5/Zstd; identity for contributors; chain of custody for releases.  
7. Community: certificate + 1–2 workshops + issue templates + first external pack recipes.  
8. Metrics for a later Track 2: external contributors, time-to-first-successful-run, documented deployments on HiPerGator/Pegasus, security checklist pass rate.

**Letters (3–5, <2 pages, third parties):** education affiliate; HPC user; clinical analyst who will *not* be paid on the award; one methods lab that might compare MHL implementations.

**Budget (~$300k, 12 months) that reviewers will accept**

- PI / community manager / tech writer (governance and docs — not kernel work)  
- Education affiliate 0.2 FTE or summer instructor  
- Student stipends for contributor onboarding (CI, fixtures, tutorials)  
- Workshop and I-Corps for PESOSE travel  
- Legal hours for license + contributor agreement  
- Modest cloud/HPC only for *demo infrastructure*, not cohort processing  

Put alignment R&D and cohort sequencing on FCIF/ITCR. If the budget justification says “implement dual-graph pangenome,” it will be scored as product development and rejected on fit.

**Keywords line** (required at end of Project Summary): e.g. `open-source ecosystems; healthcare; genomics; research software; software supply chain`.

---

## Risks specific to this company

1. **Closed or “non-profit only” core.** Instant mismatch. Publish OSI text before the January draft.  
2. **Aims that read like the partnership brief’s science section.** PESOSE reviewers are not cancer-center study-section. They score managing organizations.  
3. **No external users by March.** Downloads by the founding team do not count. The practicum and HPC pilot exist to manufacture those letters.  
4. **Dual-use confusion.** Services and commercial licenses around an open core are compatible. A proposal that treats GoliathOmics as a future closed SaMD product is not.  
5. **Track 2 envy.** ~10 awards nationwide. Without contributors and a named managing-org form, wait.  
6. **September 2026 window is gone.** Use the next six months to create the public artifact NSF requires you to point at.

---

## Practical sequence to March 2, 2027

1. Freeze OSI license + public monorepo (or clearly cited component repos) by November.  
2. Run the 20–50 sample HPC pilot so a UF/Sylvester person can write a user letter.  
3. Teach the 3-credit special topics course so two student PRs exist.  
4. Decide lead: Goliath vs Florida Poly/UF.  
5. Draft the 7-page Track 1 + security plan in January.  
6. Collect 3–5 collaboration letters in February.  
7. Submit 2 March 2027. If awarded, Track 2 in 2028 sits beside Bankhead-Coley / ITCR sustainment.

PESOSE is the federal home for the *education and governance* layer of your strategy. FCIF remains the home for Florida patients and GPUs. Do not merge the two narratives in one budget.