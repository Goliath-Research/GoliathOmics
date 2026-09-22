Wang’s program is four **different** method classes. GoliathOmics covers one of them well and only touches the others at the edges.

## Snapshot

| Wang / Moffitt technique | Completeness | Fit today |
|--------------------------|--------------|-----------|
| **cfDNA methylation** (targeted EM-Seq, MHB/MHL, mCRPC OS) | **High (~75%)** | First-class procedure |
| **Clinico-genomic scores** (labs + ctDNA fraction + methylation risk → nomogram) | **Medium (~50%)** | Cox sidecar exists; clinical calculator and CNA scores do not |
| **Exosomal miRNAs / proteins** | **Low (~10%)** | Generic RNA/proteomics packs only; no EV / small-RNA path |
| **Long-read methylation of noncoding risk variants** | **Near zero** | Explicitly deferred; not a product path |

---

### 1. Cell-free DNA methylation — mostly supported

This is the Wong / Wang 2026 plasma EM-Seq + MHB/MHL work.

**Shipped:** `cfdna` analyte; `cfdna_emseq_targeted` and `cfdna_emseq_mhl_survival`; linear EM-Seq SamplePrep; panel BED extract; `{chrom}-CG.mhap.h5`; native MHB/MHL with their defaults (window 3, \(r^2>0.3\), \(p<0.05\), ≥3 CpGs, median reads >50, MHL lengths 1–10); Cox / KM / time-AUC / optional nomogram.

**Still missing for a true Wang-lab match:**

- Their Twist panel BED, control-region BEDs, and locked 15-MHB list (operator assets, not code)
- GREAT on DMR coordinates
- **MBD-seq ultra-low-input** (Huang & Wang, *Epigenetics* 2022) — no MBD-seq SamplePrep
- **5hmC** in plasma cfDNA (Li et al. 2023, ADT-resistant PCa) — no 5hmC chemistry or calling
- Plasma **copy-number** risk scores (Huang et al. 2022 *Cancers*) — fragmentomics mentions CNV; there is no ichorCNA / cfDNA CNA gene-score action
- Bismark/mHapSuite bit-for-bit parity (intentionally native, not wrapped)

Plasma WGBS + fragmentomics (`cfdna_wgbs_plasma`) is a **different** cfDNA methylation geometry (discovery / MCED-adjacent), not their capture-MHL assay.

---

### 2. Exosomal miRNAs / proteins — not supported as their assay

Wang-adjacent work includes plasma exosomal **miR-423-3p / miR-375 / miR-1290** (CRPC prediction), EV isolation-method effects on **lipidome/metabolome**, and EV cargo more generally.

**What we have:** RNA-Seq pack = STAR/kallisto **mRNA/lncRNA gene counts**; proteomics pack = DIA-NN / Sage / Olink-style **protein abundance**. Those are bulk (or panel) modalities, not EV isolation + small-RNA.

**To add if this is a partnership pillar:**

1. Analyte or matrix: `plasma_ev` / `exosome` (isolation method as config, not a hardcoded kit)
2. **Small-RNA SamplePrep:** adapter-aware miRNA-seq (or qPCR panel ingest) → `mirna.h5` (`mature_id`, count/RPM), not STAR gene counts
3. Optional **EV-protein** ingest into the existing proteomics `abundance.h5` seam (`ingest_mode: panel`)
4. A procedure that **joins** EV-miRNA (± EV-protein) with cfDNA MHL on matched `sample_id` for Cox or tabular fusion  
   Isolation chemistry stays wet-lab; we only need the digital contract.

Do **not** treat `rna_parabricks_star_de` as “we support exosomal miRNA.”

---

### 3. Long-read methylation of noncoding risk variants — not supported

This is Tian, Wang et al.: Nanopore **native** 5mC (and 5hmC in later nanoASM), **adaptive sampling** on GWAS/mQTL intervals, haplotype-specific DMRs, allele-specific methylation vs ATAC/ChIP, large MHBs that short reads cannot span (*HGG Adv* 2025; nanoASM 2026; earlier rs7247241 / *PPP1R14A* work).

**What we have:** Illumina short-read WGBS/EM-Seq. Long-read is **deferred** in the SamplePrep plan. `mojo-align` still has experimental methylGrapher `longread.py` / `MM:Z` graph align — not a worker action, not Dorado, not adaptive sampling, not nanoASM.

**To add (large):**

1. ONT SamplePrep: pod5/BAM ingress → Dorado (5mCG ± 5hmCG) → GRCh38 (linear first; graph later)
2. Adaptive-sampling BED (GWAS + mQTL) as `target_panel_bed` analogue
3. Phasing + **allele-specific methylation** (nanoASM-class): SNP-split reads, haplotype DMRs, optional overlap with GWAS/mQTL catalogues
4. Long-read MHB/entropy on native calls (their point vs short-read MHL)
5. Optional ATAC/ChIP overlays — not required for v1

This is tissue/cell-line **functional genomics** (22Rv1, prostate tissue), not the plasma EM-Seq product path. Keep it a separate procedure (`tissue_ont_asm` or similar).

---

### 4. Clinico-genomic scores in mCRPC — half supported

Wong nomogram = MHL risk + **PSA, ALP, LDH** + **predicted ctDNA fraction** (ctdna.org: yield, labs, ECOG, mets) + nested test vs ctDNA-only.

**Shipped:** `survival_path` (`sample_id`, `time`, `event`, optional `psa`/`alp`/`ldh`/`predicted_ctdna_fraction`); Cox; time-AUC; `write_nomogram`; `nested_lrt`.

**Missing:**

- In-process **ctdna.org** (or a documented clone of that calculator)
- First-class EMR fields (ECOG, viscera mets, cfDNA yield) vs a free-form CSV
- **Plasma CNA / AR-locus** scores (Huang 2022; Du multiplex dPCR AR amp)
- CTCs, treatment-line covariates
- Their exact LOOCV \(\sum\beta_i M_i\) recipe and 0.5/1/2-year timeROC defaults as a locked Moffitt score object (we have the engine, not their published score)

HiTIMED `tumor_fraction` is **not** their ctDNA predictor.

---

## What to add, in partnership order

If the goal is “we can work with Wang’s lab,” not “we reimplement the entire CV”:

1. **Close the MHL+OS loop (small):** operator BED + survival dictionary; optional GREAT; document nested LRT + time-AUC horizons 0.5/1/2 years; keep ctdna.org as a sidecar column unless they insist on the API.
2. **Clinico-genomic v2 (medium):** typed clinical schema (PSA/ALP/LDH/ECOG/mets/yield) + optional cfDNA **CNA/ichor** action; do not invent AR-dPCR in Python.
3. **EV cargo (medium, only if they want it):** small-RNA + EV-protein panel ingest and fusion with MHL — reuse RNA/proteomics seams, new analyte/QC.
4. **Long-read ASM (large, separate product):** Dorado + adaptive sampling + haplotype ASM. Do not block plasma MHL on this.

A partnership can start on **(1)+(2)** with assets they already have. **(3)** and **(4)** are new process packs, not overlays on `cfdna_emseq_mhl_survival`.