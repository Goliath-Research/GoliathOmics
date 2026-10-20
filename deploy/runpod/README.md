# RunPod worker template (`goliath-worker`)

This directory is the RunPod adapter. The worker itself is provider-neutral: [`scripts/start_shared_worker.sh`](../../scripts/start_shared_worker.sh). How Lambda, Nebius, and a local disk call that script is [deploy/README.md](../README.md).

`deploy/targets/goliath-test.json` does not SSH to workers. The pool boots itself from a RunPod template cloned from `runpod-torch-v280` and re-pointed at the `goliath-worker` GHCR image. The container start command is one line (`deploy/runpod/template-start-command.sh`). That line downloads `deploy/runpod/goliath-worker-bootstrap.sh` from the newest non-draft `goliathomics-*` release and runs it. On an empty `/work` the bootstrap creates `/work/goliath/bin`, downloads the RunPod adapter, `scripts/start_shared_worker.sh`, and `keepalive.sh` from that release tag, and runs the adapter.

The adapter sets `GOLIATH_NODE_ID` from `RUNPOD_POD_ID` and `GOLIATH_START_KEEPALIVE` to `keepalive.sh` (which execs the image's `/start.sh`). It then execs `start_shared_worker.sh`. That script waits for `/work`, creates the shared layout (`samples`, `projects`, `cache`, `genomes`, `site`) and the shared image cache `/work/goliath/images`, uses `/work/goliath/current` when that tree is already there, otherwise downloads the latest GitHub release bundle, runs `provision_worker_node.sh` in join mode, reports `goliath-worker` to Rollouts, and starts `methyl-worker`. The keepalive then holds Jupyter (port 8888) and SSH (port 22). `/start.sh` is not used on any other cloud.

`/work` on these pods is one Runpod global volume (`work`) mounted read-write by every pod (`goliath-worker-1`, `goliath-worker-2`, …). Global volumes do not support permission bits, file locking, hard links, or atomic rename ([Runpod global volume limitations](https://docs.runpod.io/storage/globalvolume/overview)). `chmod` and `chown` return `Operation not permitted`. The scripts still request the shared modes (`0755` directories, `0644` files, or `2775` / `0664` when `GOLIATH_SHARED_GROUP` exists). A rejected change is a warning. Files are created with umask `0022` (or `0002` with that group) so other pods can read them anyway. Other filesystems, including Lambda NFS and a local disk, honor the same `chmod`.

## Shared volume layout

Shared by every pod:

| Path | Contents |
| --- | --- |
| `/work/samples`, `/work/projects`, `/work/cache` | Task inputs and outputs |
| `/work/genomes`, `/work/site` | References and the site manifest |
| `/work/goliath/releases`, `/work/goliath/current` | Release trees and the current symlink |
| `/work/goliath/images` | Shared image cache |
| `/work/goliath/bin/goliath-worker-start.sh` | Start script, run with `bash` |

Nothing executable runs from `/work`. The volume ignores exec bits, rejects `chmod`/`chown`/`utime`, and files written onto it can be listed but sometimes fail to read back. Scripts stored there are started with `bash`. Native code and Python live on container-local disk, rebuilt on every boot:

| Path (container-local) | Contents |
| --- | --- |
| `/opt/goliath/venv-amd64` | Worker Python venv (`methyl-worker`, `methyl-*` CLIs), built from the release `wheels/` and `requirements-worker.lock` |
| `/opt/goliath/methyl-extractor-amd64` | MethylExtractor binary and bundled libs |

The venv install is offline first: `wheels/` carries, best-effort, the pins in `requirements-worker.lock` (first-party plus third-party manylinux cp312 x86_64 wheels; aarch64 is opt-in via `--wheel-arches`). If the offline install fails, `worker_venv_local.sh` logs a `[WARN]` and retries with a package index (`GOLIATH_PIP_INDEX_URL`, default `https://pypi.org/simple`, plus `GOLIATH_PIP_EXTRA_INDEX_URLS`, default `https://pypi.nvidia.com` for the CUDA/RAPIDS pins), still pinned by the lock and hash-checked when the lock has hashes. `GOLIATH_WORKER_VENV_ALLOW_INDEX=0` forbids the online retry.

`GOLIATH_LOCAL_ROOT` moves both (default `/opt/goliath`; `GOLIATH_WORKER_VENV` / `METHYL_EXTRACTOR_HOME` override each). A location on the work mount, or one that cannot be written, falls back to `/tmp/goliath`.

Per-node, still on `/work` and world-readable, under `/work/goliath/nodes/$GOLIATH_NODE_ID`. On RunPod the adapter sets `GOLIATH_NODE_ID` from `RUNPOD_POD_ID`. Other clouds use the hostname, then `/etc/machine-id`. Runtime logs and the supervisor pid are not here; they are on local disk under `GOLIATH_LOG_DIR` (default `/var/log/goliath`, size-rotated):

| Path | Contents |
| --- | --- |
| `tmp/`, `scratch/` | Downloads and scratch that are not secrets |
| `env/worker.env` | Non-secret worker settings (paths, images, endpoints) |
| `status/joined` | Join stamp for this pod and release |

Secrets never go on `/work`. The GitHub auth header, `secrets.env` (storage keys and the database password), and `/etc/methyl/worker-token` live on container-local disk (`/run/goliath`, or `/tmp/goliath-private` when `/run` is not writable) with umask `077`. The container disk does not survive a pod restart, so a new container enrolls again. A restart inside the same container skips enroll when the local token and `status/joined` still match.

Two pods must not publish the same release at once. The downloader extracts into `/work/goliath/releases/.partial-<pod>`, renames that directory onto `/work/goliath/releases/<version>`, then runs `promote_release.sh`. A `mkdir` lock at `/work/goliath/locks/release-promote` serializes that critical section. Global volumes do not actually lock files, so the lock is advisory and a holder older than 30 minutes is removed. `current.ready` is written after the `current` symlink is updated. Readers should treat `current.ready` as the commit point, because rename on this volume is not atomic.

Docker overlay storage does not work on the volume. The runtime data-root stays at `/var/lib/docker` on the container disk. `/work/goliath/images` is only a shared image cache. The start script logs a warning when `daemon.json` or `DOCKER_DATA_ROOT` points at `/work`.

Container-local disk is the pod scratch. Workers stage FASTQ.gz files larger than 25 GB each, BAMs, Parabricks tmp, logs, venvs, and docker layers here. `/work` is a shared geesefs mount and must not be used for scratch. The worker profile setting `local_disk_gb` (default 200, minimum 150) is the size `create_worker.sh` requests. The v2 create field is `disk` (v1 name `containerDiskInGb`). It overrides a template that still says 30 GB. The disk disappears when the container is replaced. `/work` is the shared result volume. On boot, before MethylExtractor, GoliathAlign, and the venv are installed, `scripts/check_local_scratch.sh` refuses to start when free space on `METHYL_LOCAL_TMP` (default `/tmp/methyl-pb`) is below `METHYL_LOCAL_TMP_MIN_GB` (default 60). The 150 GB minimum leaves that floor after those installs. The same check first deletes per-sample BAM and MethylExtractor directories under `METHYL_LOCAL_TMP` with nothing modified in `METHYL_LOCAL_STALE_HOURS` (default 72; 0 disables).

Publishing a result is a workflow-step action, not something the pod boot scripts do. `methyl_worker.work_share.publish_to_work(local_src, dest)` is the helper a step calls after it finishes computing:

1. If `local_src` is on container scratch, copy it once to the sibling `<dest>.tmp-<pod>-<task>` on `/work`.
2. `rename` that sibling onto `dest` (no second copy once the bytes are on `/work`).
3. Write `<dest>.complete` after the rename returns. Runpod global volumes do not support atomic rename, so other pods should read `dest` only after that marker exists.
4. The copy is created with umask `0022` (`0002` when `GOLIATH_SHARED_GROUP` exists) and does not copy the source mode bits. `chmod` then sets `0644`/`0664` or `0755`/`2775`. A volume that ignores mode bits does not fail the publish.

The workflow engine that decides when a step stages inputs locally and when it calls `publish_to_work` lives in [GoliathApp](https://github.com/Goliath-Research/GoliathApp). This repository does not change those step definitions. Follow-up there: call `publish_to_work` from the task runtime when a step's output must land on the shared volume, and keep partial bytes off the final path.

A fresh volume does not need a manual copy of the start script. If the template line, the bootstrap, or the start script fails, the error is logged and `/start.sh` still runs, so the pod stays up for debugging instead of restart-looping.

## Clone the template

1. In the RunPod console, open **Templates** and find the template whose image is `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`. That template is `runpod-torch-v280`.
2. **Clone** it. Name the clone `goliath-worker`.
3. Set the container image to `ghcr.io/goliath-research/goliath-worker:4.7.0-1` (Clara Parabricks host `pbrun`; see `deploy/workers/image/`). Alternatively keep the template image and pass `deploy/workers/create_worker.sh --provider runpod --image ghcr.io/goliath-research/goliath-worker:4.7.0-1` so the create body sets `imageName`. Private GHCR (required — Parabricks EULA) needs a Runpod container registry credential: set `RUNPOD_REGISTRY_AUTH_ID` or `create_worker.sh --registry-auth <id>` (v2 create body field `registry`). Legacy: `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404` cannot run nested Clara Docker.
4. Set the container disk to 200 GB. That matches the worker profile `local_disk_gb`. `create_worker.sh` also sends `disk` on the create body, so a template left at 30 GB does not win. This repository does not update the live template.
5. Do not add a volume to the template. Templates can only hold a host-local `persistent` mount, and a template persistent mount cannot be combined with the global volume (`create_worker_pod.sh` refuses such a template). The global volume is attached per pod, at creation, by the script below.
6. Do not deploy pods from this template in the console. Use [Create a worker pod](#create-a-worker-pod).

## Create a worker pod

Always create worker pods with [`create_worker_pod.sh`](create_worker_pod.sh) (or the equivalent `deploy/workers/create_worker.sh --provider runpod`). Never deploy them from the Runpod console.

`create_worker_pod.sh` is a thin wrapper that runs the provider-neutral [`deploy/workers/create_worker.sh`](../workers/create_worker.sh) with `--provider runpod`. The Runpod API calls live in [`deploy/workers/providers/runpod.sh`](../workers/providers/runpod.sh); see [`deploy/workers/providers/README.md`](../workers/providers/README.md) for the provider interface.

Why:

- A Runpod template cannot carry a network or global volume mount, only a host-local `persistent` one. The global volume has to be attached to each pod when it is created.
- A console deploy mounts the global volume at `/workspace` by default (`/workspace-global` next to a network volume). The bootstrap then stops with `persistent volume /work is not mounted and writable`.
- Runpod cannot add or change a mount kind after creation (`mount type cannot be changed from none to global after pod creation`). A pod created with the wrong mount has to be terminated and created again.

The script calls the Runpod REST API v2 (`POST https://api.runpod.io/v2/pods`), which takes the global volume and its path in the create body. The v1 REST API (`rest.runpod.io/v1`) has no global volumes. The body it sends:

```json
{
  "name": "goliath-worker-2",
  "templateId": "nw0judtfil",
  "cloud": "SECURE",
  "gpu": {"id": "NVIDIA L40S", "count": 1},
  "disk": 200,
  "mounts": {"global": [{"volumeId": "<id of the global volume named work>", "path": "/work"}]},
  "startSsh": true
}
```

`disk` is the container disk in GB (v1 name `containerDiskInGb`). It comes from the provider-neutral worker profile setting `local_disk_gb` (default 200, minimum 150) via `--local-disk-gb`, `--profile`, or `GOLIATH_LOCAL_DISK_GB`. It overrides the template disk. The template still supplies the image, start command, ports, `{{ RUNPOD_SECRET_* }}` env, and CUDA constraint. `startSsh` injects the account's registered SSH keys so the printed SSH command works.

Needs `curl`, `jq`, and `RUNPOD_API_KEY` in the environment (a key with pod read/write). The key is passed to `curl` through a private config file, not the command line, and is never printed.

```bash
# Resolve the template, volume, next free name and GPU price, print the body. No POST.
deploy/runpod/create_worker_pod.sh --dry-run

# Create it (billable). Defaults: --template goliath-worker --volume work
# --mount-path /work --gpu 'NVIDIA L40S' --gpu-count 1 --cloud SECURE
# --local-disk-gb 200
deploy/runpod/create_worker_pod.sh --yes

# Another GPU type or an explicit name
deploy/runpod/create_worker_pod.sh --gpu 'NVIDIA RTX 6000 Ada Generation' --name goliath-worker-5 --yes
```

What it does:

1. Resolves `--template` (default `goliath-worker`) and `--volume` (default the global volume `work`) by name or id. It stops if either is missing or ambiguous, or if the template has a persistent mount.
2. Picks the name: `--name`, or the lowest free `goliath-worker-N` among existing pods. An existing name is refused.
3. Prints the GPU's list price per hour for the chosen cloud (`GET /v2/catalog/gpus/{id}`). The created pod's `cost` is the billed rate.
4. Refuses a mount path other than `/work` unless `--allow-nonstandard-path` is also given.
5. With `--dry-run`, prints the JSON body and exits. Without `--yes`, prints the body and exits non-zero. Only `--yes` POSTs.
6. After the POST, reads the pod back (`GET /v2/pods/{id}`) and checks that `mounts.global` has the volume at the mount path. If it does not, it prints an error, says the pod must be terminated, and exits 3. It never terminates a pod itself.
7. Waits up to `--wait` seconds (default 180) for SSH, then prints `pod_id`, `name`, `status`, `mount`, `ssh`, and `public_ip`.

`scripts/test_create_worker_pod.sh` checks the Runpod provider with a stubbed `curl` (no network), and `scripts/test_create_worker.sh` checks the generic flow with a fake provider. Both run in the PR workflow.

## Container start command

The cloned template's default command runs `/start.sh` directly. Replace the container start command with the one line in the block below. Paste it as one command. The line stays the same when the bootstrap or the start script changes.

`runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404` includes `curl` and `python3`. The line uses `python3` to call the GitHub API and decode the file.

`GH_TOKEN` (or `GITHUB_TOKEN`) is a fine-grained PAT with Contents read on `GOLIATH_GITHUB_REPO`. Python reads the token from the environment and sends it as an `Authorization` header. The token is not an argument of `python3` or `curl`, so the process list does not show it. The line does not print the token. Do not add `set -x`.

`GOLIATH_RELEASE_TAG` selects the git tag when it is set. Otherwise the line lists releases and uses the newest tag that starts with `goliathomics-`, skipping drafts and prereleases. It downloads `deploy/runpod/goliath-worker-bootstrap.sh` from that tag through the contents API (`GET /repos/{owner}/{repo}/contents/deploy/runpod/goliath-worker-bootstrap.sh?ref={tag}`). `gh release create` in `.github/workflows/release-assemble.yml` tags the repository checkout, and that workflow refuses to publish when this file is missing, so the path is on the tag. `GOLIATH_GITHUB_REPO` defaults to `Goliath-Research/GoliathOmics`. Leave `GOLIATH_GITHUB_API` unset; it defaults to `https://api.github.com`.

The downloaded bootstrap waits until `/work` is writable, creates `/work/goliath/{bin,releases,nodes}`, and then:

1. Installs `/work/goliath/current/runtime-bundle/deploy/runpod/goliath-worker-start.sh` when that file is already on the volume.
2. Otherwise runs `/work/goliath/bin/goliath-worker-start.sh` when that file is already installed.
3. Otherwise uses `GH_TOKEN` to read the GitHub release list, selects `GOLIATH_RELEASE_TAG` or the newest non-draft `goliathomics-*` tag, downloads `deploy/runpod/goliath-worker-start.sh` from that git tag through the contents API, and installs it. It also installs `scripts/start_shared_worker.sh` and `deploy/runpod/keepalive.sh` from that tag when a token is available. `chmod 0755` is attempted. A volume that rejects it logs a warning. The script is run with `bash`.

The bootstrap sends its token with a mode 600 header file on container-local disk and removes the file. It does not print the token.

If the download or the bootstrap fails, the line prints `goliath-stub-failed` and execs `/start.sh`. `GOLIATH_START_KEEPALIVE` overrides that path; leave it unset on the template. sshd on port 22 and Jupyter on port 8888 stay up, and the container does not exit, so RunPod does not restart-loop. A stop signal still ends the pod.

<!-- goliath-template-start:begin -->
```text
bash -lc 'set +x;python3 -c "import os,json,base64,re,urllib.request as u;e=os.environ.get;t=e(\"GH_TOKEN\") or e(\"GITHUB_TOKEN\") or \"\";r=e(\"GOLIATH_GITHUB_REPO\") or \"Goliath-Research/GoliathOmics\";g=e(\"GOLIATH_RELEASE_TAG\") or \"\";a=(e(\"GOLIATH_GITHUB_API\") or \"https://api.github.com\").rstrip(\"/\");assert t and chr(10) not in t and chr(13) not in t and re.fullmatch(\"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\",r);h={\"Authorization\":\"Bearer \"+t};q=lambda n: json.load(u.urlopen(u.Request(a+n,headers=h),timeout=60));g=g or next((i[\"tag_name\"] for i in q(\"/repos/\"+r+\"/releases?per_page=100\") if isinstance(i,dict) and not i.get(\"draft\") and not i.get(\"prerelease\") and str(i.get(\"tag_name\") or \"\").startswith(\"goliathomics-\")), \"\");assert re.fullmatch(\"goliathomics-[A-Za-z0-9._+-]+\",g);b=base64.b64decode(q(\"/repos/\"+r+\"/contents/deploy/runpod/goliath-worker-bootstrap.sh?ref=\"+g)[\"content\"]);assert b\"goliath-worker\" in b;open(\"/tmp/goliath-worker-bootstrap.sh\",\"wb\").write(b)"&&exec bash /tmp/goliath-worker-bootstrap.sh "$@"||{ echo goliath-stub-failed >&2;exec "${GOLIATH_START_KEEPALIVE:-/start.sh}";}'
```
<!-- goliath-template-start:end -->

`GH_TOKEN` is required on every boot so the template line can download the current bootstrap. It is also required the first time the volume has neither the bundle copy nor `/work/goliath/bin/goliath-worker-start.sh`. After a release bundle is promoted, later boots still refresh the bootstrap from the release, then prefer the start script inside `/work/goliath/current`.

## Environment variables

Set plain values in the template. Reference RunPod secrets as `{{ RUNPOD_SECRET_name }}`. Do not paste secret values into the template.

| Variable | Value |
| --- | --- |
| `WORKER_API_BASE` | `https://45.76.21.8/v1` (the profile `public_base_url`; the worker appends `/workers/...`) |
| `GOLIATH_GATEWAY_CA_PEM` | `{{ RUNPOD_SECRET_GOLIATH_GATEWAY_CA_PEM }}` — PEM of the gateway `ca.pem`. Required while the gateway TLS mode is `self-signed`. |
| `GOLIATH_ENV` | `goliath-test` |
| `GOLIATH_GITHUB_REPO` | `Goliath-Research/GoliathOmics` |
| `CLUSTER_KEY` | `goliath` |
| `B2_BUCKET` | `goliath-omics` (new data; S3 protocol) |
| `B2_ENDPOINT` | `https://s3.us-east-005.backblazeb2.com` |
| `LONGTERM_BUCKET` | `goliath-omics` (long-term storage; S3 protocol) |
| `LONGTERM_ENDPOINT` | `https://s3.us-east-1.myqnapcloud.io` |
| `LONGTERM_HOST` | `s3.us-east-1.myqnapcloud.io` |
| `LONGTERM_SHARE` | `goliath-omics` |
| `CURSOR_API_KEY` | `{{ RUNPOD_SECRET_CURSOR_API_KEY }}` |
| `GOLIATH_WORKER_TOKEN` | `{{ RUNPOD_SECRET_GOLIATH_WORKER_TOKEN }}` |
| `B2_ACCESS_KEY_ID` | `{{ RUNPOD_SECRET_B2_ACCESS_KEY_ID }}` |
| `B2_SECRET_ACCESS_KEY` | `{{ RUNPOD_SECRET_B2_SECRET_ACCESS_KEY }}` |
| `LONGTERM_ACCESS_KEY_ID` | `{{ RUNPOD_SECRET_LONGTERM_ACCESS_KEY_ID }}` |
| `LONGTERM_SECRET_ACCESS_KEY` | `{{ RUNPOD_SECRET_LONGTERM_SECRET_ACCESS_KEY }}` |
| `GH_TOKEN` | `{{ RUNPOD_SECRET_GH_TOKEN }}` (Contents read on `GOLIATH_GITHUB_REPO`. Required on every boot to fetch the bootstrap, to fetch the start script onto a fresh volume, and to download the release bundle when `/work/goliath/current` is missing) |

`B2_*` is the new-data store and `LONGTERM_*` is the long-term store. Both speak S3. The pod keeps those names. Bucket and endpoint land in the per-node `worker.env`. Access keys land only in local `secrets.env` mode `0600`. The start script does not copy either store into `AWS_*` or `GOLIATH_LONGTERM_*`. A tool that needs `AWS_ACCESS_KEY_ID` for one call takes it from the store that call uses, inside that process.

`NEON_TEST_PASSWORD` is not required on the worker. Set `GOLIATH_WORKER_NEEDS_DATABASE=1` and `NEON_TEST_PASSWORD={{ RUNPOD_SECRET_NEON_TEST_PASSWORD }}` only if this pod must open the database itself. The gateway holds the database password.

`GOLIATH_WORKER_CAPABILITY` is optional. Leave it unset so `methyl-worker enroll` probes the GPU host (that probe uses `nvidia-smi`). Set it only to pin a catalog capability such as `parabricks.fq2bam`. The Rollouts message always includes the GPU name `nvidia-smi` printed, plus `RUNPOD_POD_ID`, the hostname, and `arch`.

`RUNPOD_POD_ID` is injected by RunPod. Do not set it. The adapter copies it into `GOLIATH_NODE_ID`. The shared start script does not read `RUNPOD_*`.

## Gateway certificate

goliath-test has no DNS name. The gateway deploy issues a self-signed certificate for `45.76.21.8` and keeps it at `/etc/ssl/methyl-gateway/ca.pem`. `methyl-worker` and `requests` will not trust that certificate until the pod has the PEM.

After the first gateway deploy that installs nginx:

```bash
ssh -i <deploy-key> linuxuser@45.76.21.8 sudo cat /etc/ssl/methyl-gateway/ca.pem
```

Create a RunPod secret named `GOLIATH_GATEWAY_CA_PEM` whose value is that PEM, including the `BEGIN CERTIFICATE` and `END CERTIFICATE` lines. In the template, set `GOLIATH_GATEWAY_CA_PEM` to `{{ RUNPOD_SECRET_GOLIATH_GATEWAY_CA_PEM }}`.

On boot the start script writes the PEM to `/work/goliath/env/gateway-ca.pem` (world-readable; `chmod 644` is best-effort) and does not print it. When a system CA bundle is present it also writes `/work/goliath/env/requests-ca-bundle.pem` (the system bundle plus the gateway certificate) and exports `REQUESTS_CA_BUNDLE` and `SSL_CERT_FILE` to that file. `GOLIATH_GATEWAY_CA_FILE` points at the gateway certificate alone. `methyl-worker` adds `GOLIATH_GATEWAY_CA_FILE` to the default trust store for gateway HTTPS. `requests` uses `REQUESTS_CA_BUNDLE`, which still contains the system CAs, so other HTTPS calls keep working. The certificate is public. Storage keys and the worker token are not written beside it.

Restart the pod after the secret is set. A pod that was already joined still rewrites `worker.env` on boot. If you rotate the gateway certificate, delete `/etc/ssl/methyl-gateway` on the gateway, deploy again, and replace the RunPod secret with the new `ca.pem`.

Leave `GOLIATH_GATEWAY_CA_PEM` unset when `gateway.tls.mode` is `letsencrypt`. Those certificates are in the public trust store.

Optional: `GOLIATH_RELEASE_URL` is a direct tarball URL. `GOLIATH_RELEASE_TAG` selects a tag instead of the newest `goliathomics-*` release, for the template download of `goliath-worker-bootstrap.sh`, for the bootstrap download of `goliath-worker-start.sh`, and for the release bundle. Prefer leaving both unset. `GH_TOKEN` reads the newest non-draft, non-prerelease `goliathomics-*` tag and, when `/work/goliath/current` is absent, the `goliathomics-release-*.tar.gz` asset on that GitHub release.

## What the pod does on boot

1. The template command downloads `goliath-worker-bootstrap.sh` from the release tag and runs it. The bootstrap waits until `/work` is mounted and writable, installs `goliath-worker-start.sh` under `/work/goliath/bin` (from the current bundle, the existing bin copy, or the release tag), and runs it. A failure here logs and execs `/start.sh`.
2. The shared start creates the shared directories when they are missing (`samples`, `projects`, `cache`, `genomes`, `site`, and `/work/goliath/images`) and the per-node directory `/work/goliath/nodes/$GOLIATH_NODE_ID`. When the release bundle includes `init_work_layout.sh`, that script creates the shared roots and writes a default site manifest only if `/work/site/methyl_site.json` is absent. It chmods directories to `0775` (or `2775` with `GOLIATH_SHARED_GROUP`). A rejected chmod is a warning.
3. If `/work/goliath/current` exists, use it. Otherwise take the promote lock, download the release bundle (`GH_TOKEN` / `GITHUB_TOKEN`, or `GOLIATH_RELEASE_URL`) into the node tmp dir, rename it onto `/work/goliath/releases/<version>`, and run `promote_release.sh --skip-docker-pull --skip-venv`. Parabricks is not pulled on this path, and promotion writes no venv or binary onto `/work`. `current.ready` is written last. If neither current nor a token/URL is available, the script logs the error and the RunPod keepalive execs `/start.sh` so Jupyter and SSH stay up.
3a. Install MethylExtractor and the worker venv on container-local disk (`methyl_extractor_local.sh`, `worker_venv_local.sh`). The release wheels and lock are copied off `/work` and their size and sha256 checked before `pip` sees them. A stamp in the venv (`.goliath-venv-stamp`: release version, arch, lock sha256) skips an unchanged install; a new container always rebuilds. The per-node `worker.env` gets `GOLIATH_WORKER_VENV` and a `PATH` that starts with that venv.
4. Run `provision_worker_node.sh --join-mode join --arch amd64 --gpu` so enroll can probe GPU capability via `nvidia-smi`. Host apt setup and the VM e2e verifier are skipped: this image already has the CUDA stack, and those checks are for SSH-managed VMs. Re-running inside the same container with the same release and `WORKER_API_BASE` skips enroll. The worker token stays on container-local disk.
5. Report service `goliath-worker` through `report_deploy.sh`. The message looks like `node=<id> host=<hostname> arch=amd64 GPU=<nvidia-smi name>`.
6. Start `methyl-worker`. If systemd is running, install the unit. Otherwise a supervisor restarts `methyl-worker` and appends logs to the local `/var/log/goliath/methyl-worker.log` (rotated at `GOLIATH_LOG_MAX_BYTES`). The supervisor loads the shared non-secret `worker.env`, the per-node `worker.env`, and the local `secrets.env`.
7. The RunPod adapter runs `keepalive.sh` with `bash` (the copy on `/work` has no exec bit), which execs `/start.sh`. Other providers leave `GOLIATH_START_KEEPALIVE` unset and this step does not run.

The script is safe to run again on the same pod. It does not print secret values. Genome files are not downloaded at boot; `genomes/` is only the shared directory (and the default site manifest, once the release bundle is present).
