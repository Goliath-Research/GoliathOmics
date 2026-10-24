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
| `schema` | First applies the release's GoliathApp PostgreSQL scripts once per release (see [GoliathApp schema](#goliathapp-schema-once-per-release)). Then applies `workflow_engine/sql_pg/deploy_test_bed.sh` when `schema.apply` is `test-bed`. `schema.apply` `none` skips the test-bed SQL. The connection uses `database.schema_host` when set (Neon direct host) and `database.host` otherwise. `channel_binding` is exported as `PGCHANNELBINDING`. The test-bed token fixture is applied only when the target name, `rollouts_environment`, database name, or host looks like a test bed. Set `GOLIATH_ALLOW_TEST_BED_TOKEN=1` to apply it to any other database. |
| `gateway` | Writes `<install_root>/env/gateway.env` (database password and `POSTGRES_CHANNEL_BINDING` when set; default root `/opt/methyl-gateway`) and runs `install_gateway_systemd.sh --root`. The gateway keeps the pooled `database.host`. That script installs `methyl-gateway.service` and the reclaim timer. It does not take `--env-file` or `--user`. The same SSH session then runs `setup_gateway_nginx.sh`, which installs nginx if needed, installs `deploy/nginx/methyl-gateway.conf` as a proxy to `bind_host:port` (default `127.0.0.1:8080`), and reloads nginx. TLS defaults to a self-signed certificate. SSH uses the host `identity_file`. A `secret:NAME` value is the private key, written to a mode 600 file and passed with `-i`. The client sets `PasswordAuthentication=no`. |
| `runtime` | Applies the release's GoliathApp schema first when it is not recorded yet (a no-op after `schema` in `all`). Runs `promote_release.sh --root --release --arch` plus `--pull-parabricks` or `--skip-docker-pull` on SSH hosts. Self-provision workers skip this SSH promote and boot from `/work/goliath/current` or a GitHub release bundle. |
| `workers` | SSH workers: writes `/work/goliath/env/worker.env` (the path `methyl-worker.service` loads) and `<install_root>/env/worker.env`, then runs `install_worker_systemd.sh --root --arch` and `--capability` when the profile sets one. `self_provision: true` workers: no SSH. The driver prints the pool (provider, template, image, GPU, volume) and exits successfully. |

`rollouts_environment` from the profile is exported as `GOLIATH_ENV` for every report.

SSH uses `BatchMode`. The profile stores `user@host` and an optional `identity_file`: a path on the operator machine, or `secret:NAME` whose value is the private key. It does not store the key.

## GitHub Actions

**GoliathOmics release deploy** takes an optional `target` input. When it is set, the job runs `deploy_target.sh --component all` instead of the runner-local promote. The job runs on a GitHub-hosted `ubuntu-latest` runner in the GitHub environment `goliath-test`. That environment needs `CURSOR_API_KEY`, `NEON_TEST_PASSWORD`, `GOLIATH_GATEWAY_DB_PASSWORD`, `GOLIATH_PORTAL_DB_PASSWORD`, `GOLIATH_DEPLOY_SSH_KEY`, `GOLIATH_GATEWAY_KNOWN_HOSTS`, `B2_ACCESS_KEY_ID`, `B2_SECRET_ACCESS_KEY`, `LONGTERM_ACCESS_KEY_ID`, and `LONGTERM_SECRET_ACCESS_KEY`. The workflow maps those names into the job. A customer with different secret names needs those names added to the job `env` block, or the values placed in `/etc/goliath/<target>.env` on the runner. `GOLIATH_DEPLOY_SSH_KEY` is the gateway private key, not a path. `GOLIATH_GATEWAY_KNOWN_HOSTS` is not a profile `secret:NAME` and is not read from `/etc/goliath`. It is the gateway `ssh-keyscan` output, one or more lines. When `target` is set, before any SSH or scp, the workflow writes that secret to `~/.ssh/known_hosts` (`mkdir -p ~/.ssh`, directory mode 700, file mode 600) and fails if the secret is empty. The job does not run `ssh-keyscan` and does not set `StrictHostKeyChecking=no`.

The runner installs `postgresql-client` with apt when `psql` is missing, and installs `jq`, `openssh-client`, `curl`, and `python3` when those commands are missing. `gh` is the GitHub CLI already on the hosted image.

Leave `target` empty to keep the single-runner promote of an assembled bundle. That path reports service `goliathomics-runtime` once for the job. The promote script skips its own report for that service when `GOLIATH_ROLLOUTS_SKIP_SERVICES` includes it, so the bundle and the workflow do not open two deployments.

When `target` is set, the workflow start and finish steps report `goliathomics-runtime` on the profile's `rollouts_environment`. For `goliath-test` that value is `goliath-test`, the same environment `deploy_target.sh` uses for the other services, so one deploy stays on one Rollouts environment. When `target` is empty, or the profile has no usable `rollouts_environment`, those steps fall back to `production-work`. `scripts/report_deploy.sh` stays non-fatal: a failed report does not fail the job.

## goliath-test

`deploy/targets/goliath-test.json` is the test target (`rollouts_environment` `goliath-test`):

| Piece | Value |
| --- | --- |
| Database | Neon Postgres. Pooled host `ep-wandering-pine-a5l8s690-pooler.us-east-2.aws.neon.tech` for the gateway. Direct host `ep-wandering-pine-a5l8s690.us-east-2.aws.neon.tech` (`schema_host`) for schema apply. Database `GoliathOmics` (in SQL, double-quote the identifier: `"GoliathOmics"`), user `neondb_owner`, `sslmode` and `channel_binding` `require`. Password `secret:NEON_TEST_PASSWORD`. Schema apply sets login passwords for `goliath_gateway` (`secret:GOLIATH_GATEWAY_DB_PASSWORD`) and `goliath_portal` (`secret:GOLIATH_PORTAL_DB_PASSWORD`). When the gateway password is set, the gateway env uses that role instead of `neondb_owner`. |
| Schema | `schema.goliathapp` is `per-release`: the first deploy of each release applies the GoliathApp `sql_pg` scripts in the release to `GoliathOmics` over `schema_host`. `schema.apply` is `test-bed`: the test-bed SQL in this repo runs after it. |
| Gateway | Public host `45.76.21.8`, SSH user `linuxuser`, key `secret:GOLIATH_DEPLOY_SSH_KEY`, host keys in the environment secret `GOLIATH_GATEWAY_KNOWN_HOSTS`, install root `/opt/methyl-gateway`. Worker API base `https://45.76.21.8/v1`. TLS mode `self-signed`, SAN IP `45.76.21.8`. Nginx proxies public 443 to `127.0.0.1:8080`. |
| Shared storage | Backblaze B2 bucket `goliath-omics`, endpoint `https://s3.us-east-005.backblazeb2.com`, region `us-east-005`. Keys `secret:B2_ACCESS_KEY_ID` and `secret:B2_SECRET_ACCESS_KEY`. The schema has no bucket-id field. |
| Long-term storage | myQNAPCloud, serving both hot and cold (`roles`: `hot`, `cold`). Bucket and share `goliath-omics`, endpoint `https://s3.us-east-1.myqnapcloud.io`, host `s3.us-east-1.myqnapcloud.io`. Keys `secret:LONGTERM_ACCESS_KEY_ID` and `secret:LONGTERM_SECRET_ACCESS_KEY`. |
| Workers | No SSH hosts. One self-provisioning RunPod pool, `goliath-worker-1`. |

## GoliathApp schema once per release

GoliathApp's `workflow_engine/sql_pg` scripts are the database definition. They ship inside the release and are applied by the deploy, not patched into the database by hand.

- **Build.** `build_release.sh` calls `build_gateway_wheel.sh --sql-out <release>/goliathapp-sql`. From the same GoliathApp checkout as the `methyl-gateway` wheel, `scripts/bundle_goliathapp_sql.py` copies the scripts listed in the `SCRIPTS=( … )` array of `sql_pg/apply_schema.sh`, in that order. GoliathApp's own apply script is the only list. The bundle holds `sql_pg/*.sql`, `manifest.sha256` (apply order with digests), and `source.json` (GoliathApp commit). `manifest.json` gets `goliathapp_sql` (`path`, `commit`, `scripts`). The release workflow fails when the bundle is missing. `assemble_release.sh` verifies the digests and keeps the bundle and the manifest field.
- **Deploy.** `deploy_target.sh --component schema`, `runtime` or `all` runs `scripts/apply_goliathapp_schema.sh --release-dir $GOLIATH_RELEASE_SRC` on the deploy runner, which holds the database secret. Pods never run SQL. The gateway host stays a REST gateway and receives neither the scripts nor the release bundle.
- **Once.** The table `wf.schema_release` (`release`, `goliathapp_sha`, `manifest_sha256`, `script_count`, `applied_at_utc`, `applied_by`, `apply_count`) records each applied release, keyed by the `manifest.json` version (the `goliathomics-<ver>` bundle). A release already in the table is skipped. So the first deploy of a release to `/work` upgrades the database, and later deploys of that release (re-runs, single components) leave it alone. If the bundle's GoliathApp commit differs from the recorded one, the skip prints a warning.
- **Atomic.** The ledger table, every script, and the ledger row run in one transaction (`psql -v ON_ERROR_STOP=1`) under an advisory lock. A failing script rolls everything back, nothing is recorded, `deploy_target.sh` stops before gateway and runtime, and the next deploy tries again. `GOLIATH_SCHEMA_LOCK_TIMEOUT` (default `120s`) bounds lock waits.
- **Legacy EpiPortal tables.** Some `LANGUAGE sql` functions read `portal."Customers"`, `portal."Role2Node"`, or `portal."Diseases"`, which exist only on databases migrated from EpiPortal. The apply sets `check_function_bodies = off` in its transaction (as `pg_dump` output does), so those functions are created everywhere and fail only when called on a database without those tables. No script is skipped.
- **Force.** `--force-schema` (or `GOLIATH_SCHEMA_FORCE=1`, or the `forceSchema` input of **GoliathOmics release deploy**) applies a recorded release again and increments `apply_count`.
- **Off.** `schema.goliathapp: none` disables the step for a target. Without `GOLIATH_RELEASE_SRC` there is no bundle and the step is skipped. A release built before this change has no `goliathapp-sql/` and is skipped with a warning.

The scripts must be re-runnable on an existing database, because every release applies the full ordered list. `scripts/test_apply_goliathapp_schema.sh` checks the ledger on a scratch PostgreSQL cluster. With `GOLIATH_APP_SQL_DIR` set (the PR workflow sets it to a checkout of GoliathApp `main`), it applies the real scripts three times to one database.

## Self-provisioning workers

A worker object with `self_provision: true` is a pool that boots itself. `deploy_target.sh --component workers` and `--component all` do not open an SSH session to it. They print the provider, template, image, GPU, vCPU, RAM, and volume, then return success. SSH workers in the same list are still installed over SSH.

`goliath-worker-1` is provider `runpod`, arch `amd64`, GPU RTX 6000 Ada × 1 (48 GB), 16 vCPU, 62 GB RAM, container-local disk `local_disk_gb` 200 (minimum 150), volume `/work`, template `goliath-worker`, image `ghcr.io/goliath-research/goliath-worker:4.7.0-1`. `local_disk_gb` is the scratch disk for FASTQs, BAMs, Parabricks tmp, logs, venvs, and docker layers. `/work` is the shared volume and is not scratch. The pod start command and the RunPod secret names are in [deploy/runpod/README.md](../deploy/runpod/README.md). That command bootstraps an empty `/work` volume. On boot the pod runs `provision_worker_node.sh --join-mode join --arch amd64 --gpu` and reports `goliath-worker` itself. The message includes `RUNPOD_POD_ID`, hostname, arch, and the GPU name from `nvidia-smi`. Before MethylExtractor, GoliathAlign, and the venv are installed, boot refuses to start when free space on `METHYL_LOCAL_TMP` (default `/tmp/methyl-pb`) is below 60 GB. The 150 GB minimum leaves that floor after those installs.

## Gateway TLS

`methyl-gateway` stays on `gateway.bind_host` and `gateway.port` (`127.0.0.1:8080` for goliath-test). Workers use `gateway.public_base_url`. `setup_gateway_nginx.sh` is what makes that URL answer:

- Installs the `nginx` package when the `nginx` command is missing.
- Installs `deploy/nginx/methyl-gateway.conf` (HTTP redirects to HTTPS; `/v1/` proxies to the loopback gateway) and reloads nginx. Re-running the deploy does this again.
- When `ufw` is already active, allows `80/tcp` and `443/tcp`. It does not enable `ufw` when `ufw` is inactive, and it does not change SSH rules.
- Curls `https://127.0.0.1/v1/` through nginx and fails the deploy when that check does not get an HTTP response from the gateway (connection failure, or HTTP 502/504).

`gateway.tls` is optional. Omitted, or `"mode": "self-signed"`, issues a certificate once under `/etc/ssl/methyl-gateway/` (`fullchain.pem`, `privkey.pem`, and `ca.pem`). The subjectAltName is `gateway.tls.san_ip` when set, otherwise the host of `public_base_url` when that host is an IPv4 address, otherwise that DNS name. A later deploy reuses the key. To rotate it, delete `/etc/ssl/methyl-gateway` on the gateway and deploy again.

`gateway.tls.mode` `letsencrypt` is the optional path when a DNS name exists. Set `gateway.tls.domain` and `gateway.tls.email`, and point `public_base_url` at `https://<domain>/v1`. The domain must resolve to the gateway. An IP-only gateway does not need this mode.

The Vultr cloud firewall, if this instance is assigned to one, is not `ufw`. That group has to allow inbound TCP 443. Allow TCP 80 as well when you want the HTTP redirect or Let's Encrypt HTTP-01. The loopback deploy check does not pass through that firewall.

RunPod workers trust a self-signed gateway certificate by pinning `ca.pem`. See [deploy/runpod/README.md](../deploy/runpod/README.md).

## Public gateway SSH

`45.76.21.8` is a public address. SSH to the gateway is key-only.

On the gateway, `sshd_config` must contain:

```text
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
```

Reload `sshd` after that change. The deploy client also passes `-o PasswordAuthentication=no -o PreferredAuthentications=publickey -o IdentitiesOnly=yes` and `-i` the key from `secret:GOLIATH_DEPLOY_SSH_KEY`. Do not commit the key. Put it in the `goliath-test` GitHub environment (the release-deploy job's environment) or in `/etc/goliath/goliath-test.env` on the runner.

Host key checking stays on. `ssh_cmd` in `scripts/deploy_target.sh` sets `UserKnownHostsFile` to `~/.ssh/known_hosts`. `scp_to` copies files through that same SSH session, so it uses the same file. Capture the lines on a trusted network with `ssh-keyscan 45.76.21.8` (add `-H` to store a hashed host) and save that output as the `goliath-test` environment secret `GOLIATH_GATEWAY_KNOWN_HOSTS`. The value is one or more lines, the same text `ssh-keyscan` prints. Do not commit the lines. The release-deploy job writes the secret to `~/.ssh/known_hosts` before it SSHes and stops if the secret is empty. It does not run `ssh-keyscan` and does not set `StrictHostKeyChecking=no`.
