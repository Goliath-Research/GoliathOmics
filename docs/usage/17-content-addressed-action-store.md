# Content-Addressed Action Store (CAAS)

## Purpose

Version idempotent workflow action results by **hyperparameter and input signature** on shared storage. Study actions reuse one physical copy under `.caas/`. Sample-scoped align/prep actions keep FASTQ/BAM/tar at their canonical paths and record a ledger manifest only — do not relocate multi-GB files on NFS.

CAAS complements (does not replace) per-output-dir manifests at `{output_dir}/.action_results/`.

## Default behavior

CAAS is **enabled by default** for every project and workflow run. No environment variable or task flag is required.

### When to disable

- **Per instance / task:** `"caasEnabled": false` in instance context or task input. Sample-scoped align/prep can also be rejected with `"sampleCaasEnabled": false`.
- **Worker / site opt-out:** `METHYL_CAAS_ENABLED=0` (also accepts `false`, `no`, `off`). Sample-scoped store: `METHYL_SAMPLE_CAAS_ENABLED=0`.

`METHYL_SAMPLE_CAAS_ENABLED=0` turns off the sample ledger (and the old harvest-move path on workers that have not picked up in-place commits). Study CAAS is unchanged.

## Content key

```
content_key = sha256(action_revision + "|" + input_signature)
```

`input_signature` is **cumulative**: path normalization applies study `path_remap`, collapses dual NFS mounts (`/lambda/nfs/Work` → `/work`), then resolves symlinks, so upstream CAAS outputs and multi-mount workers share keys. Any config or upstream artifact change forks a new store entry; prior results remain under their old keys.

Directory fingerprints used inside signatures prefer **content digests** (size + hash / head-tail for large files), not mtime-only, so NFS remounts do not spuriously fork keys.

## On-disk layout

`{project_root}` = `{output_base}/{project_name}` from the study manifest.

```
{project_root}/.caas/
  {action_safe}/{content_key}/
    <product artifacts...>
    manifest.json
  foreach_bundle/{content_key}/
    manifest.json
  instances/{hyperparamSetId}.json
```

| Path | Meaning |
|------|---------|
| `{project_root}/.caas/{action_safe}/{content_key}/` | Immutable product artifacts for one action execution |
| `{project_root}/.caas/{action_safe}/{content_key}/manifest.json` | `ActionExecutionRecord` (schema 1.2) with `content_key`, `hyperparam_set_id` |
| `{project_root}/.caas/foreach_bundle/{content_key}/` | FOREACH iteration-bundle short-circuit record |
| `{project_root}/.caas/instances/{hyperparamSetId}.json` | Instance ledger: `{action}:{run_key}` → `content_key` |
| `{output_dir}/.action_results/{action}.{run_key}.json` | Canonical skip/replay manifest (may reference CAAS paths) |
| `/work/samples/{sample_id}/.caas/` | Sample-scoped **ledger** for align/prep (`manifest.json` only). FASTQs stay at `/work/samples/{id}/`; BAM/QC stay in `sampleDir` (aligner leaf when bound). Reject with `sampleCaasEnabled: false` or `METHYL_SAMPLE_CAAS_ENABLED=0`. |

Study canonical output paths (e.g. under `run_0001/detections/...`) become **symlinks** into `.caas/` after a successful commit. Sample FASTQ/BAM/tar are **not** moved.

## Hyperparameter set identity

`finalize_instance_context` bakes `executionScopeId` from all merged `resolvedConfig__*` slices (optional label via `executionScopeName`; legacy `hyperparamSetId` still accepted as an alias). Every ACTION template receives `executionScopeId`; workers stamp it on manifests and append ledger entries.

Use the ledger to compare which content keys each execution scope resolved to (for example Balanced Accuracy per scope).

## Database mirror (distributed gateway)

When the gateway and workers run against PostgreSQL or Azure SQL:

- `wf.execution_scope` — scope identity and merged config snapshot (formerly `hyperparameter_set`)
- `wf.workflow_instance.execution_scope_id` — links instance to scope
- `wf.execution_scope_action_entry` — action/run_key → content_key

Deploy: `workflow_engine/sql_pg/wf_execution_scope.sql` (parity in `sql_mssql/`). Instance creation calls `wf_apply_execution_scope`; successful task submits upsert action entries when CAAS is enabled. The wf engine stays process-agnostic (an "execution scope"); the domain "hyperparameter search / trial" concept lives in `cfg` — see [portal remote control](../architecture/portal-remote-control.md).

## Enabled actions

CAAS is **default-on** for every catalog ACTION unless an explicit opt-out applies. The committed action catalog exports `idempotency_enabled` and optional `idempotency_opt_out_reason` per action (`methyl-export-action-catalog`).

| Class | Eligibility |
|-------|-------------|
| `pipeline.*`, `validation.*`, `context.*`, typed `workflow.const_*` / `json_path_*` | Eligible (study `.caas/`) |
| `workflow.fs_stat` | Opt-out: `mtime_sensitive` |
| `sample.delete_*`, `sample.qc_failed` | Hard opt-out (destructive / control-flow) |
| `sample.archive_sample` | Hard opt-out: `remote_upload_etag_only` (QNAP etag/md5 skip is not CAAS; it does not restore BAMs into `/work`) |
| Other `sample.*` / `parabricks.*` / `proteomics.*` / `align.*` / `methylgrapher.*` | Eligible (sample `/work/samples/{id}/.caas/`). Reject only with `sampleCaasEnabled: false`, `caasEnabled: false`, `METHYL_SAMPLE_CAAS_ENABLED=0`, or `forceRerun` |

### FOREACH iteration bundles

Before fan-out, the local scheduler (and DB engines via `wf.foreach_bundle_entry`) probe an **iteration-bundle** content key for the FOREACH BODY item. On hit, the BODY is marked skipped without claiming leaf ACTIONs. On miss, normal fan-out runs; a successful BODY commits the bundle under `.caas/foreach_bundle/`.

Disable with `METHYL_FOREACH_CAAS_ENABLED=0` or `"foreachCaasEnabled": false` in context. `--force-rerun` / `forceRerun` also bypasses bundle short-circuit.

### Split-reuse bridge

`validation.plan_iterations` and `validation.model_mc` signatures include split-CSV fingerprints (`methyl_validation.reuse_splits.fingerprint_*`). When CAAS hits, workers relink products; the older `[split-reuse]` / `Kept existing` paths remain as in-process fallbacks when CAAS is off or signatures miss. `requireArtifactReuse` stays a strict execution gate inside model-MC.

## Observability

```bash
python scripts/audit_caas_skip_rate.py /work/projects/<study>/<project_name>
python scripts/audit_caas_skip_rate.py /work/projects/<study>/<project_name> --json
```

## Skip, reuse, and force re-run

1. Worker computes `content_key` from catalog revision + validated input.
2. If a valid CAAS entry exists, study products are relinked (sample products are already at `sampleDir`) and the action is skipped (`skipped: true` in `action_run_log.jsonl`).
3. On fresh execution, study product artifacts are committed to `.caas/` and canonical paths are symlinked. Sample-scoped commits write `manifest.json` only.

Force a fresh entry (ignore skip):

```bash
methyl-workflow-run --program path/to/program.json --context-file profile.json --force-rerun
```

Or set `"forceRerun": true` in context / per-action input, or `METHYL_FORCE_RERUN=1`.

## Observability checklist

- [ ] No opt-out is set (`caasEnabled` / `sampleCaasEnabled` not false; `METHYL_CAAS_ENABLED` / `METHYL_SAMPLE_CAAS_ENABLED` not `0`/`false`)
- [ ] `{project_root}/.caas/` appears after first successful idempotent study action
- [ ] `/work/samples/{sample_id}/.caas/` appears after first successful align/prep action
- [ ] `.action_results/*.json` records include `content_key` and `hyperparam_set_id` (schema 1.2)
- [ ] Second instance with unchanged upstream config reuses entries (check `skipped: true` in `action_run_log.jsonl`)
- [ ] Config change produces a new `content_key` without deleting the old store entry

## See also

- [Artifacts and QA checks](10-artifacts-and-qa-checks.md) — stage artifact map and handoff gates
- [DomainProgram language — CAAS](../reference/domain-program-language.md#content-addressed-action-store-caas)
- [Universal CAAS idempotency plan](../plans/universal-caas-idempotency.plan.md)
- [Hyperparameter result versioning plan](../plans/hyperparameter-result-versioning.plan.md)
- `packages/methyldomain/methyl_domain/content_store.py` — commit, relink, ledger implementation
- `packages/methyldomain/methyl_domain/foreach_bundle.py` — FOREACH iteration-bundle store
- `packages/methyldomain/methyl_domain/sample_content_store.py` — sample-scoped CAAS (default-on for align/prep)
