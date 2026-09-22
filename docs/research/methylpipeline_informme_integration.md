# Integrating informME information-theoretic measures into MethylPipeline

> Informal design/research note. **Not** canonical user or operator documentation.
> It records how the information-theoretic methylation analysis of informME
> (Jenkinson, Abante, Feinberg & Goutsias) could be used inside MethylPipeline —
> both as an orthogonal confirmation layer and as new modeling features — given
> that MethylPipeline already retains the upstream data informME actually consumes.

## What informME produces

informME (<https://github.com/GarrettJenkinson/informME>, GPLv3; MATLAB + C++/MEX + R)
models methylation as an **Ising system** over neighboring CpGs and emits genome-wide
information-theoretic tracks:

- **Single-sample:** mean methylation level (MML), **normalized methylation entropy (NME)**,
  entropic sensitivity index (ESI), methylation sensitivity index (MSI), channel capacity,
  turnover ratio, relative dissipated energy (RDE), plus mean/entropy-based region classifications.
- **Differential (pheno1 vs pheno2):** dMML, dNME, and the **Jensen–Shannon distance (JSD)**.
- **Postprocessing:** JSD-based **DMR detection**, and **gene / region ranking** by average
  mutual information (`rankGenes`, `rankRegions`).

The scientific value is that these move analysis *beyond mean shifts* to **differentially
variable / entropic** regions — precisely where cancer destabilizes the epigenome (PMD
landscape flattening in Jenkinson et al., *Nat. Genet.* 2017).

## The data-model gap (the one thing that matters)

informME's Ising estimation needs **read-level joint CpG states**: the `getMatrices.sh` step
parses the BAM into per-region −1/0/1 matrices recording *which CpGs are co-methylated on the
same read*. That co-methylation structure is what identifies the α/β/γ parameters and yields
entropy/JSD.

MethylPipeline's per-sample archive `{chr}-{ctx}.h5` stores only **marginal per-CpG counts**
(`pos`, `mC`, `uC`, `tnc`; see `methyl_centroid` / `MethylSample`). Marginals are aggregated
over reads, so joint co-methylation is **not recoverable** from the `.h5`.
`methylderivedmeasures` today computes only *marginal* surrogates — binary Shannon entropy
(`genome::global_entropy`) and an adjacent-CpG disagreement `pdr_proxy` — not the Ising quantities.

**Consequence:** the required input is the **aligned, deduplicated BAM**
(`/work/samples/{sample_id}/{sample_id}.bam`), which MethylPipeline retains — *not* the centroid
`.h5`. Any integration must read BAMs (or have MethylExtractor emit read-level patterns), never
the marginal counts.

## What we already have that maps to informME inputs

| informME step | Needs | Already present |
|---|---|---|
| `fastaToCpg.sh` | reference FASTA → CpG coords/density/distance | `/work/genomes/...GRCh38...fa`; `pos`/`tnc` already encode much of this |
| `getMatrices.sh` | sorted, indexed, dedup BAM | `{sample_id}.bam` (Parabricks `fq2bam_meth`, dedup) — the exact required input |
| `informME_run.sh` | BAM matrices grouped by phenotype | cohort/group/comparison structure in `project_*.json` |
| DMR / gene / region ranking | replicate reference vs test tracks | multi-sample replicate cohorts (informME's preferred mode) |

No FASTQs, re-alignment, or coverage downsampling are required. informME marks low-coverage
subregions "modeled / not modeled" rather than capping, so high-depth reads are not thrown away
to meet a uniform coverage target.

## Two ways to use it

### 1. Orthogonal confirmation (low coupling)

Run the information-theoretic method on the same BAMs and treat its output as an independent
second opinion:

- **DMP/DMR corroboration.** Compare MethylPipeline's per-comparison ECDF DMPs against JSD-based
  DMRs and dNME/dMML. Independent detector (Ising + Jensen–Shannon vs ECDF two-sample test) →
  the *independent reproduction* the comparison note says the field lacks.
- **Gene-ranking corroboration.** Compare informME's mutual-information gene ranking against the
  mapper's `gene_importance` (rank correlation, top-K overlap) — a quantitative validation of the
  mapper's heuristic weight propagation.

### 2. New information-theoretic features (higher payoff)

- Feed **NME, ESI/MSI, channel capacity, RDE, and differential JSD/dNME** into
  `methylderivedmeasures` as genome/chromosome features for the sample-level classifier,
  replacing today's marginal `global_entropy` / `pdr_proxy` with true Ising-based measures.
- Add an **entropy/discordance axis** to detection: MethylPipeline separates significance from
  effect size; dNME/JSD adds *differentially variable* regions a mean-shift test misses.

## Integration options

1. **External subprocess (fastest, weakest fit).** Run informME's own `getMatrices.sh` →
   `informME_run.sh` → `*ToBed.sh` on existing BAMs, import bedGraph/BigWig as a project sidecar.
   Downsides: MATLAB + C++/MEX + R toolchain, autosomes-only, hg19-era gene annotation, and GPLv3
   (keep it a subprocess boundary — do not vendor its source into packages).
2. **Native Python embedded in the pipeline (preferred).** Reimplement the Ising / entropy / JSD
   estimation in Python as a new package (e.g. `methylinfotheory` or an extension of
   `methylderivedmeasures`), GRCh38-native, clean-room (no GPL source reuse). Enabled by:
3. **MethylExtractor read-level output (upstream enabler).** MethylExtractor
   (`/home/ubuntu/MethylExtractor`, native C, maintained separately) currently emits only marginal
   `mC/uC`. Add an option to additionally emit **per-region read × CpG joint patterns** alongside
   the marginals, so the native Python layer can estimate the Ising model without re-parsing BAMs.
   The user has confirmed MethylExtractor changes are feasible.

## Caveats to plan around

- **Genome build.** `fastaToCpg`/`getMatrices`/`informME_run` are build-agnostic (fine on GRCh38);
  informME's *gene ranking* hardcodes hg19 `TxDb...knownGene`. A native reimplementation avoids this.
- **Autosomes only** in informME's model — matches MethylPipeline's typical autosomal focus.
- **Replicates.** Best DMR/ranking modes want replicate reference tracks; MethylPipeline cohorts
  fit this, reinforcing the "replicates over depth" argument in the comparison note.
- **Licensing.** informME is GPLv3. Running it as an external tool is fine; embedding requires a
  clean-room Python reimplementation of the Ising estimation.

## v2 equilibrium Ising in `methylinfotheory` (implemented)

When `actionConfig.info_measures.ising_enabled` is true, `pipeline.info_measures` fits a
**per-tile max-entropy / Ising model** to the same `{chrom}-{ctx}.patterns.h5` sidecars (no
contract change). Outputs extend v1:

| Layer | Artifacts / columns |
|-------|---------------------|
| Sample covariates | `readlevel::global_{mml,nme,esi,msi}` and per-chromosome variants in `readlevel_measures.csv` |
| Cohort differential | `ising_regions.csv` with dMML, dNME, model-based JSD, mutual information per tile |
| Confirmation | `confirmation_report.json` adds `top_dnme`, `dmp_concordance_dnme`, `gene_concordance_mi` |

Implementation: `packages/methylinfotheory/methyl_infotheory/core/{ising,ising_measures,differential}.py`.
GPU batching uses `methyl_utils.array_backend.get_array_module` (CuPy when available;
`METHYL_DISABLE_GPU=1` forces CPU). Tiles are batched as dense `(n_tiles, 2^k)` tensors.

## Dynamic measures deferred (phase 2 scaffold)

Channel capacity, relative dissipated energy (RDE), and turnover ratio from the Nat. Genet. 2017
birth–death / potential-energy model are **not** computed in v2 phase 1. Setting
`dynamics_enabled: true` records `{"status": "not_computed", "reason": "deferred_v2_phase2"}`
in the confirmation report; signatures live in `core/dynamics.py` for a later cut behind the
same `pipeline.info_measures` action.

## Sources

- README + release notes: <https://github.com/GarrettJenkinson/informME> (`uploads/informME-0.md`).
- Jenkinson G., Pujadas E., Goutsias J., Feinberg A.P. (2017). "Potential energy landscapes
  identify the information-theoretic nature of the epigenome." *Nat. Genet.* 49:719–729.
- Jenkinson G., Abante J., Feinberg A.P., Goutsias J. (2018). "An information-theoretic approach to
  the modeling and analysis of whole-genome bisulfite sequencing data." *BMC Bioinformatics* 19:87.
- Jenkinson G., Abante J., Koldobskiy M., Feinberg A.P., Goutsias J. (2019). "Ranking genomic
  features using an information-theoretic measure of epigenetic discordance." *BMC Bioinformatics*
  20:175.
- MethylPipeline data path: `docs/implementation/sample-preparation-flow.md`,
  `workers/methyl_worker/extract_runner.py`, `packages/methylderivedmeasures/`.
