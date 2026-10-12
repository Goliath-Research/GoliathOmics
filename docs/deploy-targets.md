# Deploy targets

One JSON profile plus one GitHub environment describes a customer setup, including a distributed test setup:

- Neon Postgres for the workflow database
- A Vultr host for the gateway
- Backblaze B2 as shared storage for parallel workers
- RunPod or myQNAPCloud for long-term storage
- Several worker VMs, each with its own arch and capability

Infrastructure from that list is not a Rollouts service. Service ids and the API key are documented in [rollouts.md](rollouts.md).

## Profile

Profiles live in `deploy/targets/<name>.json`. The schema is `deploy/targets/target.schema.json`. `deploy/targets/goliath-test.example.json` is a placeholder profile for dry runs. Copy it to `deploy/targets/<name>.json` before a real deploy. The driver refuses to apply an `*.example.json` profile unless `--dry-run` is set.

Secrets appear only as references:

```text
secret:NEON_TEST_PASSWORD
```

The file must not contain passwords, access keys, or API tokens. `scripts/deploy_target.sh` resolves each reference from the process environment first (GitHub environment secrets) and otherwise from `/etc/goliath/<target>.env` (mode 600, root-only). Already-set environment variables win over the file.

## Driver

```bash
scripts/deploy_target.sh --target goliath-test --dry-run
scripts/deploy_target.sh --target goliath-test --component gateway
scripts/deploy_target.sh --target goliath-test --component all
```

`--component` is `gateway`, `workers`, `runtime`, `schema`, or `all`.

| Component | What it does |
| --- | --- |
| `schema` | Applies `workflow_engine/sql_pg/deploy_test_bed.sh` when `schema.apply` is `test-bed`. Engine DDL from GoliathApp must already be loaded. `schema.apply` `none` skips SQL. The test-bed token fixture is applied only when the target name, `rollouts_environment`, database name, or host looks like a test bed. Set `GOLIATH_ALLOW_TEST_BED_TOKEN=1` to apply it to any other database. |
| `gateway` | Writes `<install_root>/env/gateway.env` (database password only; default root `/opt/methyl-gateway`) and runs `install_gateway_systemd.sh --root`. That script installs `methyl-gateway.service` and the reclaim timer. It does not take `--env-file` or `--user`. |
| `runtime` | Runs `promote_release.sh --root --release --arch` plus `--pull-parabricks` or `--skip-docker-pull`. |
| `workers` | Writes `/work/goliath/env/worker.env` (the path `methyl-worker.service` loads) and `<install_root>/env/worker.env`, then runs `install_worker_systemd.sh --root --arch` and `--capability` when the profile sets one. |

`rollouts_environment` from the profile is exported as `GOLIATH_ENV` for every report.

SSH uses `BatchMode`. The profile stores `user@host` and an optional path to a private key. It does not store the key.

## GitHub Actions

**GoliathOmics release deploy** takes an optional `target` input. When it is set, the job runs `deploy_target.sh --component all` instead of the runner-local promote. The job's GitHub environment is `production-work`. Map each `secret:NAME` the profile uses into that job (the workflow already maps the placeholder names from the example profile). A customer with different secret names needs those names added to the job `env` block, or the values placed in `/etc/goliath/<target>.env` on the runner.

Leave `target` empty to keep the single-runner promote of an assembled bundle. That path reports service `goliathomics-runtime` once for the job. The promote script skips its own report for that service when `GOLIATH_ROLLOUTS_SKIP_SERVICES` includes it, so the bundle and the workflow do not open two deployments.
