# Shared worker

Each cluster has two storage systems:

- Shared read/write storage mounted at `/work` (override `GOLIATH_WORK_MOUNT`). Genomes, samples, and the release tree live here. It is for sharing input and output data only: nothing executes from it and runtime logs are not written to it. Every machine in the cluster can read what another machine writes.
- New data in an S3-compatible bucket. The inputs are `B2_BUCKET`, `B2_ENDPOINT`, `B2_ACCESS_KEY_ID`, and `B2_SECRET_ACCESS_KEY`. On the current cluster that bucket is Backblaze B2. S3 is the protocol.
- Long-term objects in a second S3-compatible bucket. The inputs are `LONGTERM_BUCKET`, `LONGTERM_ENDPOINT`, `LONGTERM_HOST`, `LONGTERM_SHARE`, `LONGTERM_ACCESS_KEY_ID`, and `LONGTERM_SECRET_ACCESS_KEY`. On the current cluster that bucket is myQNAPcloud. The two stores keep separate keys.

The gateway URL is `WORKER_API_BASE`. The start script does not pick a cloud. A provider adapter may set a node id and a keepalive command, then exec the shared script.

## Shared start

`scripts/start_shared_worker.sh` waits for `/work`, publishes the release under `/work/goliath` when `current` is missing, joins the gateway, and starts `methyl-worker`. Systemd is used when it is running. Otherwise a supervisor restarts the worker and logs to local disk.

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
| `GOLIATH_LOG_DIR` | Local log directory. Default `/var/log/goliath` (falls back to `/tmp/goliath-logs`). A path on `/work` is refused. |
| `GOLIATH_LOG_MAX_BYTES`, `GOLIATH_LOG_KEEP` | Size rotation of `methyl-worker.log`: default 50 MiB per file, 3 rotated files. |
| `METHYL_LOCAL_TMP` | Local scratch root. Default `/tmp/methyl-pb`. A path on the shared work mount is refused. |
| `METHYL_LOCAL_TMP_MIN_GB` | Free space required on that filesystem before the worker starts, and before MethylExtractor, GoliathAlign, and the venv are installed. Default 60. The create minimum disk is 150 GB so this floor still holds after those installs. |

Permissions are applied with `chmod` and, when a shared group is set, `chgrp`. Directories are `0755` (or `2775` with the group). Files are `0644` (or `0664`). If the filesystem rejects the change (expected on a shared global volume), the script logs one `note:` line per boot and continues; it is not an error. Create-time umask is `0022`, or `0002` with the group, so a volume that ignores mode bits still leaves world-readable files. Local secrets are mode `0600`. A failure to protect a secret is fatal.

### Releases and `current`

Releases are `/work/goliath/releases/<version>`. `/work/goliath/current` is a pointer file holding the release path (shared volumes cannot store symlinks). Promotion writes a temp pointer next to it and renames it over `current` (`mv -T`, one `rename(2)`), so readers see the old or the new release, never a missing one. After promoting, the start re-execs the provider adapter from the release it just installed (explicit `releases/<version>` path, passed on as `GOLIATH_RELEASE_DIR`), never from a re-read of `current`.

### What stays on `/work` per node

`/work/goliath/nodes/<id>/` keeps only small state the next boot reads back: `status/joined` (skip re-provisioning), `env/worker.env` (non-secret EnvironmentFile for the worker), and `tmp/` (release download staging before it is published under `releases/`). Logs and the supervisor pid file are local (`GOLIATH_LOG_DIR`). Read a worker's log on that machine (`/var/log/goliath/methyl-worker.log`) or through `journalctl -u methyl-worker` under systemd.

### Rollouts reporting

`scripts/report_deploy.sh` reports deploys to Cursor Rollouts (key exchange at `api2.cursor.sh/auth/exchange_user_api_key`, then `factory.v1.DeploymentsService` on `api.cursor.com`). It never blocks or fails a boot: without `CURSOR_API_KEY` it logs one INFO line and skips. Each request is bounded (`REPORT_DEPLOY_CONNECT_TIMEOUT` 5 s, `REPORT_DEPLOY_MAX_TIME` 15 s, `REPORT_DEPLOY_ATTEMPTS` 2 on transport errors). These calls trust the OS CA bundle (`REPORT_DEPLOY_CA_BUNDLE` overrides) and ignore `CURL_CA_BUNDLE`/`SSL_CERT_FILE`, which the worker points at the gateway bundle. A failure with no HTTP response (`HTTP 000`) logs curl's exit code and message plus the CA inputs, never the key.

Workflow steps publish task output with `methyl_worker.work_share.publish_to_work(local_src, dest)`. That helper copies once from local scratch onto a sibling on the destination filesystem, renames it into place, and writes `<dest>.complete`. The step definitions that call it live in [GoliathApp](https://github.com/Goliath-Research/GoliathApp). Follow-up there: call `publish_to_work` when a step's output must land on `/work`.

## Worker system tools

Every worker needs the external tools listed in [`scripts/system_tools.list`](../scripts/system_tools.list) (samtools, bedtools, fastp, pigz, setfacl from `acl`, python3.12, curl, tar, ...). That file is the only list. The installer and the check both read it, and `scripts/test_release_bundles.sh` fails when worker code looks up a tool that the list does not have.

The shared start (`scripts/start_shared_worker.sh`) runs `scripts/install_system_deps.sh` after the release is in place and before anything else. This happens on every provider:

1. Tools already on PATH are skipped. When every tool is present, nothing runs.
2. The release carries offline system-deps bundles in `system-deps/<os>-<version>-<arch>/` (for example `/work/goliath/releases/<release>/system-deps/ubuntu-24.04-amd64/`). Each bundle is a small flat apt repository (`*.deb`, `Packages`, `Release`, `SHA256SUMS`, `bundle.json`). When one matches this host, the start copies it to local disk (`/var/tmp`), checks it against `SHA256SUMS`, and has apt install the missing tools' packages from it in one transaction. Every configured source, package list, pin and archive cache is hidden, so this never touches the network. Host packages change only where a dependency needs it: an older library is upgraded to the bundled build (for example `libtinfo6` for `libncurses6`), and nothing is removed. If the host has a newer build of a library that a bundled package needs at its exact bundled version, that library moves down to the bundled build with a loud `WARNING`, but only when apt can do that without removing or breaking anything. Otherwise the installer prints a `CONFLICT` report that names what would break and goes to step 3. Nothing is executed from `/work`, and packages land on the root filesystem.
3. When there is no matching bundle, or the bundle cannot provide every tool, the start falls back to the online package manager (`apt-get`, else `dnf`/`yum`/`apk`). It retries and waits for the apt/dpkg lock.
4. If a tool is still missing after that, the start fails and names the tool. A worker never starts in a state where it would skip pipeline steps.

The installer needs root or passwordless sudo. Run `install_system_deps.sh --check` to only report what is missing.

**Release side.** `scripts/build_release.sh` builds each bundle with `scripts/bundle_system_deps.sh` inside a `ubuntu:24.04` container, using docker or podman. The bundle holds the **complete** dependency closure of the manifest packages. apt resolves it against an empty dpkg status, from the `noble`, `noble-updates` and `noble-security` pockets, so a package is never dropped just because the build image already has it. Every exact-version dependency inside the set is met by the set itself, and a from-scratch `apt-get --simulate` against the finished bundle proves that before the bundle is written. For `ubuntu-24.04-amd64` this is about 73 debs and 26 MB. Before the release is accepted, `scripts/check_system_deps_bundle.sh` installs the bundle with `docker run --network none` and runs every manifest tool with `--version` in three images: stock `ubuntu:24.04`; the oldest noble package set (`ubuntu:24.04` with every package moved back to the release pocket, which is the skew that broke `runpod/pytorch`); and each worker image named in `deploy/targets/*.json` (today `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`). Any failure fails the release. The worker image is large, about 10.6 GB compressed and 3-5 minutes to pull on a GitHub runner. `release.yml` frees runner disk first. When the container storage still lacks room (3x the compressed size + 5 GB), that image is skipped with a warning and the stock and oldest checks still gate the release. Use `--require-worker-image-check` to make the skip fatal, `--system-deps-check-images` to pick the images, and `--skip-system-deps-check` for dev builds only. Add more targets with `--system-deps-targets` (for example `ubuntu-24.04-arm64`, which needs qemu on an x86 runner). `.deb` bundles were chosen over static binaries or a conda-pack tarball for these reasons: they are small, they get distro security fixes, and they install with the same apt the fallback uses. A host on another OS uses the online fallback.

The release also has to install the worker venv offline. `build_release.sh` builds a wheel for each pin that only exists as an sdist (for example `python-louvain==0.16`) with `scripts/bundle_sdist_wheels.py`, and adds that wheel's sha256 to the lock. It then fails the build unless `pip install --no-index --find-links wheels/ -r requirements-worker.lock` resolves for every bundled arch (`scripts/check_offline_wheels.sh`). Missing wheels are an error by default. `--allow-missing-wheels` is for development only.

## Plug in another provider

Call `scripts/start_shared_worker.sh` from the machine's own service manager. Do not set `RUNPOD_*` and do not point the keepalive at `/start.sh` unless that image actually has it.

**Lambda.** Shared NFS is already mounted at `/work` (the older `/lambda/nfs/Work` path is the same tree). Install the release with `scripts/provision_worker_node.sh` or let `start_shared_worker.sh` promote `current`. Run it from systemd (`scripts/install_worker_systemd.sh` once the release is present). Set `GOLIATH_NODE_ID` to the instance name. Set `GOLIATH_SHARED_GROUP` to the group the fleet shares, so directories are `2775`. Leave `GOLIATH_START_KEEPALIVE` unset. Set the same `B2_*` and `LONGTERM_*` variables the rest of the cluster uses.

**Nebius.** Same contract. Mount the cluster's shared disk at `/work`, set `B2_*` for new data and `LONGTERM_*` for long-term objects, and start `scripts/start_shared_worker.sh` from systemd. Set `GOLIATH_NODE_ID` from the instance id. No RunPod adapter.

**A local disk or an on-prem box.** Use a directory as the shared mount (`GOLIATH_WORK_MOUNT=/srv/goliath-work` is enough when there is only one machine). Point `B2_*` and `LONGTERM_*` at the S3-compatible endpoints for new data and long-term objects. `GOLIATH_NODE_ID` defaults to the hostname. `chmod` succeeds here, so the modes above are the ones on disk.

**RunPod.** The adapter is `deploy/runpod/`. The template stub, the bootstrap, `RUNPOD_POD_ID`, and `/start.sh` stay in that directory. See [deploy/runpod/README.md](runpod/README.md). Create worker pods with `deploy/runpod/create_worker_pod.sh` (a wrapper for `deploy/workers/create_worker.sh --provider runpod`), never from the Runpod console: only the API can mount the global volume at `/work`.

**Creating workers on any provider.** `deploy/workers/create_worker.sh --provider <name>` is the one entry point. It owns the provider-neutral flow (next free `goliath-worker-N` name, shared storage required at `/work`, container-local disk `local_disk_gb` default 200 GB and minimum 150, price display, `--dry-run`, the `--yes` gate, mount verification after create, and the `pod_id=`/`name=`/`status=`/`mount=`/`ssh=`/`public_ip=` summary). Each cloud is one file, `deploy/workers/providers/<name>.sh`, that overrides the hooks declared in `providers/_interface.sh`. Only `runpod` exists today; [deploy/workers/providers/README.md](workers/providers/README.md) explains how to add Lambda, Nebius, AWS, Azure, GCP or others. Real FASTQ.gz files are larger than 25 GB each. That scratch stays on the container disk (`METHYL_LOCAL_TMP`, default `/tmp/methyl-pb`). `/work` is the shared mount and is not scratch. Boot checks free space before MethylExtractor, GoliathAlign, and the venv are installed, and refuses to start when it is below `METHYL_LOCAL_TMP_MIN_GB` (default 60). The 150 GB minimum leaves that floor after those installs.
