# Shared worker

Each cluster has two storage systems:

- Shared read/write storage mounted at `/work` (override `GOLIATH_WORK_MOUNT`). Genomes, samples, the release tree, and node logs live here. Every machine in the cluster can read what another machine writes.
- New data in an S3-compatible bucket. The inputs are `B2_BUCKET`, `B2_ENDPOINT`, `B2_ACCESS_KEY_ID`, and `B2_SECRET_ACCESS_KEY`. On the current cluster that bucket is Backblaze B2. S3 is the protocol.
- Long-term objects in a second S3-compatible bucket. The inputs are `LONGTERM_BUCKET`, `LONGTERM_ENDPOINT`, `LONGTERM_HOST`, `LONGTERM_SHARE`, `LONGTERM_ACCESS_KEY_ID`, and `LONGTERM_SECRET_ACCESS_KEY`. On the current cluster that bucket is myQNAPcloud. The two stores keep separate keys.

The gateway URL is `WORKER_API_BASE`. The start script does not pick a cloud. A provider adapter may set a node id and a keepalive command, then exec the shared script.

## Shared start

`scripts/start_shared_worker.sh` waits for `/work`, publishes the release under `/work/goliath` when `current` is missing, joins the gateway, and starts `methyl-worker`. Systemd is used when it is running. Otherwise a supervisor restarts the worker and logs under `/work/goliath/nodes/<id>/`.

| Variable | Role |
| --- | --- |
| `WORKER_API_BASE` | Gateway URL. Required. |
| `GOLIATH_WORK_MOUNT` | Shared mount. Default `/work`. |
| `GOLIATH_ROOT` | Release root. Default `$GOLIATH_WORK_MOUNT/goliath`. |
| `GOLIATH_NODE_ID` | This machine's directory name. Hostname, then `/etc/machine-id`, when unset. |
| `GOLIATH_SHARED_GROUP` | When this group exists, directories on `/work` are `2775` and files are `0664` (umask `0002`), and `chgrp` is attempted. |
| `GH_TOKEN` or `GOLIATH_RELEASE_URL` | Download the release when `current` is absent. |
| `B2_BUCKET`, `B2_ENDPOINT` | New-data bucket and S3 endpoint. Written to the node `worker.env`. |
| `B2_ACCESS_KEY_ID`, `B2_SECRET_ACCESS_KEY` | New-data keys. Written only to local `secrets.env` mode `0600`. |
| `LONGTERM_BUCKET`, `LONGTERM_ENDPOINT`, `LONGTERM_HOST`, `LONGTERM_SHARE` | Long-term bucket, S3 endpoint, host, and share. Written to the node `worker.env`. |
| `LONGTERM_ACCESS_KEY_ID`, `LONGTERM_SECRET_ACCESS_KEY` | Long-term keys. Written only to local `secrets.env` mode `0600`. |
| `GOLIATH_START_KEEPALIVE` | Executable to exec after the worker is up, and after a fatal error. Unset: success exits 0, failure exits 1. |
| `GOLIATH_START_NO_EXEC` | Exit 1 on failure instead of running the keepalive. Tests set this. |

Permissions are applied with `chmod` and, when a shared group is set, `chgrp`. Directories are `0755` (or `2775` with the group). Files are `0644` (or `0664`). If the filesystem rejects the change, the script logs a warning and continues. Create-time umask is `0022`, or `0002` with the group, so a volume that ignores mode bits still leaves world-readable files. Local secrets are mode `0600`. A failure to protect a secret is fatal.

Workflow steps publish task output with `methyl_worker.work_share.publish_to_work(local_src, dest)`. That helper copies once from local scratch onto a sibling on the destination filesystem, renames it into place, and writes `<dest>.complete`. The step definitions that call it live in [GoliathApp](https://github.com/Goliath-Research/GoliathApp). Follow-up there: call `publish_to_work` when a step's output must land on `/work`.

## Plug in another provider

Call `scripts/start_shared_worker.sh` from the machine's own service manager. Do not set `RUNPOD_*` and do not point the keepalive at `/start.sh` unless that image actually has it.

**Lambda.** Shared NFS is already mounted at `/work` (the older `/lambda/nfs/Work` path is the same tree). Install the release with `scripts/provision_worker_node.sh` or let `start_shared_worker.sh` promote `current`. Run it from systemd (`scripts/install_worker_systemd.sh` once the release is present). Set `GOLIATH_NODE_ID` to the instance name. Set `GOLIATH_SHARED_GROUP` to the group the fleet shares, so directories are `2775`. Leave `GOLIATH_START_KEEPALIVE` unset. Set the same `B2_*` and `LONGTERM_*` variables the rest of the cluster uses.

**Nebius.** Same contract. Mount the cluster's shared disk at `/work`, set `B2_*` for new data and `LONGTERM_*` for long-term objects, and start `scripts/start_shared_worker.sh` from systemd. Set `GOLIATH_NODE_ID` from the instance id. No RunPod adapter.

**A local disk or an on-prem box.** Use a directory as the shared mount (`GOLIATH_WORK_MOUNT=/srv/goliath-work` is enough when there is only one machine). Point `B2_*` and `LONGTERM_*` at the S3-compatible endpoints for new data and long-term objects. `GOLIATH_NODE_ID` defaults to the hostname. `chmod` succeeds here, so the modes above are the ones on disk.

**RunPod.** The adapter is `deploy/runpod/`. The template stub, the bootstrap, `RUNPOD_POD_ID`, and `/start.sh` stay in that directory. See [deploy/runpod/README.md](runpod/README.md).
