# Copy legacy SamplePrep runs into a new database

`scripts/migrate_legacy_sampleprep_runs.py` copies completed workflow runs from the legacy GoliathOmics database into another GoliathOmics database (for example `GoliathOmics_v2`). It exists so the SamplePrep test runs from October 2026 (project `SamplePrep_e2e_clara_linear_canary`, sample `canary-SRR28293403-linear-subset`) show up in the new portal.

It copies run history only. It does not create customers, patients, samples, studies or any other clinical rows. The study label and sample ids stay where the engine recorded them: in each instance's `context_json`, and in the provenance extension described below.

## What is copied

For each selected source instance (default ids `14`–`19`, which must be `SamplePrepPipeline` and `COMPLETED`):

| Source | Target |
|---|---|
| `wf.workflow_instance` | new row; status, `context_json` and start/end timestamps unchanged |
| `wf.node_execution` | new rows, parents first; node ids remapped by `node_key` |
| `wf.execution_context`, `wf.loop_state` | new rows with remapped execution and node ids |
| `wf.scope_variable` | new rows; scope `0` (instance scope) kept, other scopes remapped |
| `wf.instance_extension` | copied with `wf.wf_repo_upsert_instance_extension` |
| `wf.execution_scope` | reused by `set_key`, or created with `wf.wf_repo_upsert_execution_scope` |

`completed_by_worker_id` is set to `NULL` because legacy workers are not enrolled in the target; the legacy worker id per copied node execution is kept in the provenance extension.

The tool stops (and writes nothing) if a selected instance has rows in tables it does not copy: `wf.instance_cursor`, `wf.execution_scope_action_entry`, `cfg.study_instance_link`, `cfg.hyperparameter_trial`, `wf.event_delivery`, `wf.test_bed_*`.

## Workflow versions

Legacy version ids are never reused. Each legacy version is fingerprinted (sha256 over node keys and types, action names, node settings, edges, input templates, input/output bindings, scope defaults, collection bindings and event triggers; ids and version numbers excluded) and compared with every target version of the same definition name.

* **Match:** the copied runs point at the matching target version.
* **No match:** the legacy graph is created with the official `wf.wf_repo_create_workflow_graph` as an **inactive** version `0.<major × 1000 + minor>` under the target definition (legacy `v2.0` becomes `0.2000`). Major `0` with `is_active = false` means the imported graph never becomes the version new runs start on and never outranks current versions. The tool re-reads the imported graph and fails if its fingerprint differs from the legacy one.

## Idempotency and safety

* **Dry run by default.** Without `--apply` the whole copy runs in one target transaction and is rolled back, so the report shows exactly what would be inserted. Identity sequences still advance (PostgreSQL does not roll those back).
* **One transaction** with `pg_advisory_xact_lock`; any error rolls back everything.
* **Reruns are no-ops.** Every copied instance gets the extension `legacy.migration.source` with `source_label`, `source_instance_id`, source and target version ids, the graph fingerprint, `study_label` and `sample_ids`. Instances already recorded under the same `--source-label` are skipped.
* **Source is read only.** The source connection is opened with `read_only = True` and `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`, and the tool checks `transaction_read_only = on` before reading. It refuses to run when source and target are the same database.

## Run

Connection strings come from environment variables, never from the command line:

```bash
export LEGACY_SOURCE_DSN=...        # legacy GoliathOmics (read only)
export MIGRATION_TARGET_DSN=...     # e.g. GoliathOmics_v2
python scripts/migrate_legacy_sampleprep_runs.py              # dry run, prints a JSON report
python scripts/migrate_legacy_sampleprep_runs.py --apply      # commit
python scripts/migrate_legacy_sampleprep_runs.py --apply      # rerun: inserted counts are all 0
```

Options: `--instance-ids 14-19`, `--workflow-name SamplePrepPipeline`, `--source-label GoliathOmics-legacy`, `--source-dsn-env`, `--target-dsn-env`.

Take a Neon backup branch of the target before `--apply` on a shared database.

## Why raw inserts

There is no engine API that records a finished run: `wf.sp_start_instance` and the gateway start new runs that workers would execute. Run history (`workflow_instance`, `node_execution`, `execution_context`, `loop_state`, `scope_variable`) is therefore inserted directly into the `wf` tables. Graph creation, execution scopes and instance extensions use the official `wf.wf_repo_*` functions.

## After migrating

* The copied instances keep their original October 2026 start times. The portal run list (`portal.sp_session_list_recent_instances`, called with a 168-hour window by the platform host) only shows runs from the last seven days, so these runs do not appear there until that window is widened.
* `study_name` in run lists comes from `cfg.study_instance_link`; the legacy runs had no study link, so it stays empty.
* A later `deploy_workflow_definitions.sh` with `delete_instances=true` would delete the copied runs with the definition; the default keep-instances deploy keeps them.
