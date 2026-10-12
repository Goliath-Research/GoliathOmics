# Cursor Rollouts deploy events

GoliathOmics reports deploys with `scripts/report_deploy.sh`. A failed report never fails the deploy: the helper uses short curl timeouts and always exits 0. Without `CURSOR_API_KEY`, `curl`, or `jq` it warns and does nothing.

## Services

These are the Rollouts service ids. Each deploy script opens one deployment for its service and closes it when the script exits.

| Service | When it is reported |
| --- | --- |
| `goliath-gateway` | `scripts/install_gateway_systemd.sh` after the unit restarts and is active |
| `goliathomics-runtime` | `scripts/promote_release.sh` when it exits after flipping `current` and writing `worker.env`. The **GoliathOmics release deploy** workflow reports the same service for the whole job. |
| `goliath-worker` | `scripts/install_worker_systemd.sh` and the end of `do_systemd` in `scripts/provision_worker_node.sh`. The message includes hostname, arch, and capability. A self-provisioning RunPod pod reports the same service from `deploy/runpod/goliath-worker-start.sh`; that message also includes `RUNPOD_POD_ID` and the GPU name from `nvidia-smi`. |
| `goliath-reclaim-leases` | `scripts/install_reclaim_leases_timer.sh` |
| `workflow-definitions` | `scripts/deploy_workflow_definitions.sh` and `scripts/deploy_mc_workflow_definitions.sh` |
| `process-pack-catalog` | `scripts/deploy_process_pack_catalog.sh` |
| `methyl-extractor` | `scripts/install_methyl_extractor_tarball.sh` |
| `goliath-db-schema` | `workflow_engine/sql_pg/deploy_test_bed.sh` |

The actor recorded on each event is `user@hostname`. The environment defaults to `$GOLIATH_ENV` or `production-work`. The version defaults to a SHA field on the release manifest (`sha`, `commit`, `git_sha`, `source_sha`, or `revision`) or `git rev-parse HEAD`.

## One environment per customer

Each customer setup is its own Rollouts environment. The test setup is not the production environment. `deploy/targets/<name>.json` sets `rollouts_environment`, and `scripts/deploy_target.sh` exports that value before it runs install scripts. See [deploy targets](deploy-targets.md).

`production-work` is the environment used by the runner-local promote in `.github/workflows/release-deploy.yml`.

## API key

Create a Cursor user API key for a member of this team.

On each VM that should report (gateway and workers):

```bash
sudo install -d -m 755 /etc/goliath
sudo tee /etc/goliath/rollouts.env >/dev/null <<'EOF'
CURSOR_API_KEY=replace-with-the-real-key
EOF
sudo chmod 600 /etc/goliath/rollouts.env
```

The committed template is `deploy/env/rollouts.env.example`. Do not commit a real key.

GitHub Actions reads the repository or environment secret **`CURSOR_API_KEY`** (the name the reporter accepts, and no other). The release-deploy job runs in the `production-work` GitHub environment, so the secret must be available there.

## What is not a service

These are dependencies of a target. They do not get their own Rollouts service:

- Neon Postgres (the workflow database)
- The Vultr virtual machine that hosts the gateway process
- Backblaze B2, the shared bucket workers use for parallel scratch
- RunPod or myQNAPCloud, the long-term archive

The gateway *process* is reported as the `goliath-gateway` service. On the VM the unit installed from GoliathWorkflow is `methyl-gateway.service` (workers are `methyl-worker.service`, lease reclaim is `methyl-reclaim-leases.timer`). Those unit names are not the Rollouts service ids. The Vultr machine is only the place that process is installed.

`GOLIATH_ROLLOUTS_EXTERNAL=1` or a service listed in `GOLIATH_ROLLOUTS_SKIP_SERVICES` makes that script skip its own report. `deploy_target.sh` sets `GOLIATH_ROLLOUTS_EXTERNAL=1` on the remote install so the driver reports once. `provision_worker_node.sh` `do_systemd` does the same for the nested `install_worker_systemd.sh` call. A direct run of those scripts on a VM still reports.

## Supersedes the draft reporter

This bash helper replaces the approach in draft pull request #1 (`scripts/report_rollouts_deploy.py`, service id `goliathomics`). The release-deploy workflow still reports the production promote, now as `goliathomics-runtime`. That draft is left open for the maintainers.
