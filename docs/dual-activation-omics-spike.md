# Dual activation spike (Omics): pub/sub wake + claim

**Issue:** [GoliathOmics #67](https://github.com/Goliath-Research/GoliathOmics/issues/67)  
**Platform:** [GoliathApp #22](https://github.com/Goliath-Research/GoliathApp/issues/22), design in [`docs/dual-activation.md`](https://github.com/Goliath-Research/GoliathApp/blob/main/docs/dual-activation.md) ([PR #23](https://github.com/Goliath-Research/GoliathApp/pull/23)); Postgres LISTEN/NOTIFY broker in [GoliathApp PR #24](https://github.com/Goliath-Research/GoliathApp/pull/24) (`WF_WAKE_POSTGRES=1` / `WORKER_WAKE_POSTGRES=1`, poll remains safety net).

## Recommendation

**Adopt (opt-in on the platform broker; no Omics-side reimplementation).**

Turn on tier-1 dual activation on the **methyl-gateway** and **methyl-worker** fleet when the deployed GoliathApp gateway wheel includes PR #24. Keep the existing poll loop as fallback. Omics should **not** ship a separate wake channel or change claim SQL in this repository.

**Expected impact:** meaningful for **idle→claim** latency on short, chained steps and when the fleet is partially idle; **small relative to wall clock** for a single sample whose bottleneck is align or extract (often tens of minutes to hours). The win scales with **number of READY transitions per hour** across SamplePrep FOREACH samples and validation MC fan-out, not with one long GPU job in isolation.

**Defer:** GGS-style tier-2 product wake topics, automatic “FASTQ landed” triggers, and any Omics-specific NOTIFY schema until tier-1 is proven in `goliath-test`.

---

## 1. Event-triggered vs backlog-drain paths

| Path | What creates work | How a node becomes claimable | Omics repo evidence |
|------|-------------------|------------------------------|---------------------|
| **SamplePrep start** | Operator or portal queues `cfg.study_start_request`; `methyl-study-start drain-requests` bakes context and calls `portal.sp_create_and_start_instance` | First per-sample steps (e.g. `sample.download_fastq`) enter the engine queue as `READY` | [`docs/operators/start-sample-prep.md`](operators/start-sample-prep.md), [`workflow_engine/ops/study_start_queue.py`](../workflow_engine/ops/study_start_queue.py) |
| **Direct admin start** | `methyl-study-start sample-prep-start` / `validation-start` | Same SQL start path; no separate worker trigger | [`workflow_engine/admin/study_start.py`](../workflow_engine/admin/study_start.py) |
| **DomainProgram edges** | Predecessor action **submits**; engine runs SEQUENCE / IF / FOREACH and marks successors `READY` | Successor claims are **pure backlog drain** from the worker’s perspective | [`workflow_engine/contract/SamplePrepFlow.md`](../workflow_engine/contract/SamplePrepFlow.md) (graph + pin rules); fixtures under [`workflow_engine/domain/fixtures/`](../workflow_engine/domain/fixtures/) |
| **FASTQ / object upload** | **Not** an in-repo event source | Portal does not upload; operator places objects at the baked `fastqSource`, then **Retry** or start | [`docs/usage/11-troubleshooting-and-recovery.md`](usage/11-troubleshooting-and-recovery.md) |
| **Instrument / LIMS callback** | **Not implemented** in GoliathOmics | Would be portal or cfg integration outside this spike | No handler or webhook in `workers/` or `workflow_engine/` |
| **Study validation lifecycle** | Queued after SamplePrep or started explicitly | Mix of one planner node (`validation.plan_iterations`) and large parallel FOREACH (centroid / detector / …) | [`workflow_engine/domain/fixtures/study_validation_lifecycle.program.json`](../workflow_engine/domain/fixtures/study_validation_lifecycle.program.json), [`docs/usage/04-orchestration-workflow-run.md`](usage/04-orchestration-workflow-run.md) |
| **Modality SamplePrep variants** | Same start patterns; different programs | RNA (`sample_prep_rnaseq`), proteomics (`sample_prep_proteomics`), EM-Seq, epi-GBS, remediate | Fixtures in [`workflow_engine/domain/fixtures/sample_prep_*.program.json`](../workflow_engine/domain/fixtures/) |
| **Legacy file-queue MC** | `plan-runs` / `run-task` | **Not** gateway claim path; out of scope for wake+claim | [`docs/usage/13-distributed-methyl-validation.md`](usage/13-distributed-methyl-validation.md) |

**Summary:** Almost all production Omics execution is **engine-scheduled backlog** (READY rows) drained by **`methyl-worker` poll → `POST /v1/workers/tasks/request`**. Logical “events” (start request, predecessor finished) stop at the database; workers only observe them when claim returns a task.

---

## 2. Where poll/claim lag shows up today

| Layer | Default behavior | Latency shape |
|-------|------------------|---------------|
| **Worker idle loop** | After an empty claim, `WorkerRunner` sleeps `poll_seconds` (default **5 s** from `WORKER_POLL_SECONDS`) | Uniform **0–5 s** extra wait per idle poll cycle before the next claim attempt |
| **Worker CLI** | `methyl-worker poll` (default subcommand) | Same; see [`workers/methyl_worker/runner.py`](../workers/methyl_worker/runner.py), [`workers/methyl_worker/__main__.py`](../workers/methyl_worker/__main__.py) |
| **Between SamplePrep steps (same sample)** | Pin chain: align → QC → extract → … on one worker; when step *N* completes, step *N+1* becomes `READY` immediately, but the runner may still sleep up to **one poll interval** before requesting again | **~0–5 s per handoff** on the pinned worker (often smaller if the loop claims again without sleeping because `run_once` returned true) |
| **Between samples (FOREACH)** | Catalog prefers continuing in-flight affinity keys; unpinned download/trim can move between workers | Up to **~5 s** when the chosen worker was sleeping idle |
| **Study start queue** | `drain-requests` is a **separate** cron/admin pass over `cfg.study_start_request`, not worker poll | Dominated by how often drain runs (lease default 600 s in CLI), not `WORKER_POLL_SECONDS` |
| **Saturated fleet** | Workers rarely idle between claims | Poll lag **≈ 0**; wake adds little until capacity frees |
| **Long align / extract** | GPU/CPU runtime dominates | Poll lag is **negligible vs step duration** (see §3) |

Smoke scripts mirror the same default instance poll interval (**5 s**): [`scripts/smoke_sample_prep.sh`](../scripts/smoke_sample_prep.sh).

---

## 3. Wake vs compute-dominated jobs

For a single WGBS sample on the methylGrapher or Parabricks path:

- **Align + extract** often dominate wall clock (tens of minutes to hours on fleet hardware).
- **One** 5 s idle gap before align starts is usually **&lt;1%** of sample time.
- **Many** short steps (download, QC JSON, register expression, validation planner outputs) and **many samples** multiply idle gaps: e.g. 8–12 handoffs × ~2.5 s average idle ≈ **20–30 s per sample** in the worst case, plus cross-sample effects when workers are waiting for READY work.

Wake + claim helps most when:

1. **Fleet is underutilized** (common on burst SamplePrep or MC waves).
2. **Chained short actions** follow a long action on the **same** worker (pinned SamplePrep chain after align completes).
3. **Capability-pinned units** (`methyl-worker@parabricks.fq2bam.service`) idle separately from omnibus workers—wake avoids one pool sleeping while READY work exists for its capability.

Wake does **not** shorten in-flight GPU time; it reduces **queue wait before claim**.

---

## 4. Omics pools and suggested wake channels

Workers enroll with a **capability list** ([`workers/methyl_worker/capabilities.py`](../workers/methyl_worker/capabilities.py)). Systemd supports omnibus (`methyl-worker.service`) or pinned [`methyl-worker@<capability>.service`](../deploy/systemd/methyl-worker@.service) with `WORKER_CAPABILITY=%i`.

Tier-1 platform wake is expected to key off the same **capability** (and/or wildcard) used in claim filters—exact NOTIFY topic naming lives in GoliathApp dual-activation docs; Omics only needs to align **enrollment pins** with those topics.

| Pool / unit pattern | Representative capabilities | Programs / actions |
|--------------------|-----------------------------|------------------|
| WGBS align (Clara linear) | `parabricks.fq2bam` | `sample.parabricks_fq2bam` |
| Stock pangenome align | `parabricks.giraffe` | `sample.parabricks_giraffe` |
| WGBS methylGrapher (CPU/GPU) | `methylgrapher.wgbs_align`, `methylgrapher.wgbs_gpu_align` | `sample.methylgrapher_wgbs_align` |
| Methyl extract (linear / Giraffe) | `methyl-extract` | `sample.methyl_extract` |
| methylGrapher extract | (same family as align site config) | `sample.methylgrapher_wgbs_extract` |
| QC / light I/O | `methyl-qc`, unpinned download | `sample.methyl_qc`, `sample.download_fastq` |
| RNA-seq SamplePrep | `parabricks.rna_fq2bam`, `parabricks.kallisto`, `rna-qc` | [`sample_prep_rnaseq.program.json`](../workflow_engine/domain/fixtures/sample_prep_rnaseq.program.json) |
| Proteomics SamplePrep | `proteomics.diann`, `proteomics.prosit`, `proteomics.casanovo`, `proteomics.sage` | [`sample_prep_proteomics.program.json`](../workflow_engine/domain/fixtures/sample_prep_proteomics.program.json) |
| Validation MC / freeze | `methyl-centroid`, `methyl-detector`, `methyl-mapper`, … | [`study_validation_lifecycle.program.json`](../workflow_engine/domain/fixtures/study_validation_lifecycle.program.json) |
| Omnibus dev / small fleet | `*` | [`deploy/systemd/methyl-worker.service`](../deploy/systemd/methyl-worker.service) |

No new Omics-specific NOTIFY tables are required for tier-1 if the broker publishes on capability strings already used in `wf.worker` enrollment and claim.

---

## Concrete adoption scenario (goliath-test)

**Scenario:** Buffy WGBS pangenome SamplePrep (`buffy_wgbs_pangenome_gene_fc`) with 24 samples, fleet of 4 GPU workers (pinned `methylgrapher.wgbs_gpu_align`) plus 2 omnibus workers for download/QC.

1. Deploy gateway with `WF_WAKE_POSTGRES=1` and workers with `WORKER_WAKE_POSTGRES=1` (GoliathApp PR #24); leave `WORKER_POLL_SECONDS=5` as safety net.
2. Queue starts via portal → `drain-requests` (unchanged).
3. **Measure:** p50/p95 time from `node_execution.status = READY` to claim lease (DB audit or gateway metrics), for `sample.download_fastq`, post-align `sample.methyl_qc`, and first `pipeline.centroid` after validation start.
4. **Success criterion (spike):** p95 idle→claim drops from O(poll) (~5 s) to **sub-second** on idle workers without increasing claim conflicts or lease churn.

If tier-1 meets that bar, document env flags in [Release / deploy](operators/release.md) for `goliath-test` only; promote to production after one release cycle.

---

## What Omics should not do

- Reimplement LISTEN/NOTIFY or a second broker in `workflow_engine/`.
- Replace poll with long-blocking HTTP; keep [`WorkerRunner.run_forever`](../workers/methyl_worker/runner.py) poll as fallback.
- Block SamplePrep on “upload webhooks” in this repo—ingest remains operator/cfg responsibility.

---

## Related docs

- [Deployment and distributed workflow](usage/14-deployment-and-distributed-workflow.md)
- [Start a SamplePrep instance](operators/start-sample-prep.md)
- [SamplePrep flow contract](../workflow_engine/contract/SamplePrepFlow.md)
- [Dependency direction (gateway lives in GoliathApp)](architecture/dependencies.md)
