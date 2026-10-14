# Deploy targets

One JSON profile plus one GitHub environment describes a customer setup, including a distributed test setup:

- Neon Postgres for the workflow database
- A Vultr host for the gateway
- Backblaze B2 as shared storage for parallel workers
- RunPod or myQNAPCloud for long-term storage
- Worker VMs with their own arch and capability, or a self-provisioning RunPod pool (`self_provision: true`)

Infrastructure from that list is not a Rollouts service. Service ids and the API key are documented in [rollouts.md](rollouts.md).

## Profile

Profiles live in `deploy/targets/<name>.json`. The schema is `deploy/targets/target.schema.json`. `deploy/targets/goliath-test.json` is the real test profile. `deploy/targets/goliath-test.example.json` is a shape example. When both exist, `scripts/deploy_target.sh --target goliath-test` uses the real file. The driver refuses to apply an `*.example.json` profile unless `--dry-run` is set and the real file is absent.

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
| `schema` | Applies `workflow_engine/sql_pg/deploy_test_bed.sh` when `schema.apply` is `test-bed`. Engine DDL from GoliathApp must already be loaded. `schema.apply` `none` skips SQL. The connection uses `database.schema_host` when set (Neon direct host) and `database.host` otherwise. `channel_binding` is exported as `PGCHANNELBINDING`. The test-bed token fixture is applied only when the target name, `rollouts_environment`, database name, or host looks like a test bed. Set `GOLIATH_ALLOW_TEST_BED_TOKEN=1` to apply it to any other database. |
| `gateway` | Writes `<install_root>/env/gateway.env` (database password and `POSTGRES_CHANNEL_BINDING` when set; default root `/opt/methyl-gateway`) and runs `install_gateway_systemd.sh --root`. The gateway keeps the pooled `database.host`. That script installs `methyl-gateway.service` and the reclaim timer. It does not take `--env-file` or `--user`. SSH uses the host `identity_file`. A `secret:NAME` value is the private key, written to a mode 600 file and passed with `-i`. The client sets `PasswordAuthentication=no`. |
| `runtime` | Runs `promote_release.sh --root --release --arch` plus `--pull-parabricks` or `--skip-docker-pull` on SSH hosts. Self-provision workers skip this SSH promote and boot from `/work/goliath/current` or a GitHub release bundle. |
| `workers` | SSH workers: writes `/work/goliath/env/worker.env` (the path `methyl-worker.service` loads) and `<install_root>/env/worker.env`, then runs `install_worker_systemd.sh --root --arch` and `--capability` when the profile sets one. `self_provision: true` workers: no SSH. The driver prints the pool (provider, template, image, GPU, volume) and exits successfully. |

`rollouts_environment` from the profile is exported as `GOLIATH_ENV` for every report.

SSH uses `BatchMode`. The profile stores `user@host` and an optional `identity_file`: a path on the operator machine, or `secret:NAME` whose value is the private key. It does not store the key.

## GitHub Actions

**GoliathOmics release deploy** takes an optional `target` input. When it is set, the job runs `deploy_target.sh --component all` instead of the runner-local promote. The job runs on a GitHub-hosted `ubuntu-latest` runner in the GitHub environment `goliath-test`. That environment needs `CURSOR_API_KEY`, `NEON_TEST_PASSWORD`, `GOLIATH_DEPLOY_SSH_KEY`, `B2_ACCESS_KEY_ID`, `B2_SECRET_ACCESS_KEY`, `LONGTERM_ACCESS_KEY_ID`, and `LONGTERM_SECRET_ACCESS_KEY`. The workflow maps those names into the job. A customer with different secret names needs those names added to the job `env` block, or the values placed in `/etc/goliath/<target>.env` on the runner. `GOLIATH_DEPLOY_SSH_KEY` is the gateway private key, not a path.

The runner installs `postgresql-client` with apt when `psql` is missing, and installs `jq`, `openssh-client`, `curl`, and `python3` when those commands are missing. `gh` is the GitHub CLI already on the hosted image.

Leave `target` empty to keep the single-runner promote of an assembled bundle. That path reports service `goliathomics-runtime` once for the job. The promote script skips its own report for that service when `GOLIATH_ROLLOUTS_SKIP_SERVICES` includes it, so the bundle and the workflow do not open two deployments.

When `target` is set, the workflow start and finish steps report `goliathomics-runtime` on the profile's `rollouts_environment`. For `goliath-test` that value is `goliath-test`, the same environment `deploy_target.sh` uses for the other services, so one deploy stays on one Rollouts environment. When `target` is empty, or the profile has no usable `rollouts_environment`, those steps fall back to `production-work`. `scripts/report_deploy.sh` stays non-fatal: a failed report does not fail the job.

## goliath-test

`deploy/targets/goliath-test.json` is the test target (`rollouts_environment` `goliath-test`):

| Piece | Value |
| --- | --- |
| Database | Neon Postgres. Pooled host `ep-wandering-pine-a5l8s690-pooler.us-east-2.aws.neon.tech` for the gateway. Direct host `ep-wandering-pine-a5l8s690.us-east-2.aws.neon.tech` (`schema_host`) for schema apply. Database `GoliathOmics` (in SQL, double-quote the identifier: `"GoliathOmics"`), user `neondb_owner`, `sslmode` and `channel_binding` `require`. Password `secret:NEON_TEST_PASSWORD`. |
| Schema | `schema.apply` is `test-bed`. Engine DDL is already applied; this step only runs the test-bed SQL in this repo. |
| Gateway | Public host `45.76.21.8`, SSH user `linuxuser`, key `secret:GOLIATH_DEPLOY_SSH_KEY`, install root `/opt/methyl-gateway`. Worker API base `https://45.76.21.8/v1`. |
| Shared storage | Backblaze B2 bucket `goliath-omics`, endpoint `https://s3.us-east-005.backblazeb2.com`, region `us-east-005`. Keys `secret:B2_ACCESS_KEY_ID` and `secret:B2_SECRET_ACCESS_KEY`. The schema has no bucket-id field. |
| Long-term storage | myQNAPCloud, serving both hot and cold (`roles`: `hot`, `cold`). Bucket and share `goliath-omics`, endpoint `https://s3.us-east-1.myqnapcloud.io`, host `s3.us-east-1.myqnapcloud.io`. Keys `secret:LONGTERM_ACCESS_KEY_ID` and `secret:LONGTERM_SECRET_ACCESS_KEY`. |
| Workers | No SSH hosts. One self-provisioning RunPod pool, `goliath-worker-1`. |

## Self-provisioning workers

A worker object with `self_provision: true` is a pool that boots itself. `deploy_target.sh --component workers` and `--component all` do not open an SSH session to it. They print the provider, template, image, GPU, vCPU, RAM, and volume, then return success. SSH workers in the same list are still installed over SSH.

`goliath-worker-1` is provider `runpod`, arch `amd64`, GPU RTX 6000 Ada × 1 (48 GB), 16 vCPU, 62 GB RAM, volume `/work`, template `runpod-torch-v280`, image `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`. The pod start command and the RunPod secret names are in [deploy/runpod/README.md](../deploy/runpod/README.md). On boot the pod runs `provision_worker_node.sh --join-mode join --arch amd64 --gpu` and reports `goliath-worker` itself. The message includes `RUNPOD_POD_ID`, hostname, arch, and the GPU name from `nvidia-smi`.

## Public gateway SSH

`45.76.21.8` is a public address. SSH to the gateway is key-only.

On the gateway, `sshd_config` must contain:

```text
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
```

Reload `sshd` after that change. The deploy client also passes `-o PasswordAuthentication=no -o PreferredAuthentications=publickey -o IdentitiesOnly=yes` and `-i` the key from `secret:GOLIATH_DEPLOY_SSH_KEY`. Do not commit the key. Put it in the `goliath-test` GitHub environment (the release-deploy job's environment) or in `/etc/goliath/goliath-test.env` on the runner.
