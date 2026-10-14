# RunPod worker template (`goliath-worker`)

`deploy/targets/goliath-test.json` does not SSH to workers. The pool boots itself from a RunPod template cloned from `runpod-torch-v280`. The pod start script is `deploy/runpod/goliath-worker-start.sh`. It waits for the `/work` volume, uses `/work/goliath/current` when that tree is already there, otherwise downloads the latest GitHub release bundle, runs `provision_worker_node.sh` in join mode, reports `goliath-worker` to Rollouts, starts `methyl-worker`, and execs the image's `/start.sh` so Jupyter and SSH keep working.

`/work` is the network volume. It persists across pod restarts. The container disk does not. The enrollment token is copied to `/work/goliath/runpod/<RUNPOD_POD_ID>/worker-token` (mode 600) and restored to `/etc/methyl/worker-token` on the next boot. A restart of the same pod skips enroll when that token and the join stamp still match the release and `WORKER_API_BASE`.

## Clone the template

1. In the RunPod console, open **Templates** and find the template whose image is `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`. That template is `runpod-torch-v280`.
2. **Clone** it. Name the clone `goliath-worker`.
3. Leave the container image as `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`.
4. Attach a network volume and set its mount path to `/work`.
5. When you deploy a pod from this template, choose GPU **RTX 6000 Ada** × 1 (48 GB), **16 vCPU**, and **62 GB** RAM. Those match the `goliath-worker-1` pool in the target profile.

## Container start command

The cloned template's default command runs `/start.sh` directly. Replace the container start command with:

```text
bash -lc 'if [[ -x /work/goliath/current/runtime-bundle/deploy/runpod/goliath-worker-start.sh ]]; then exec bash /work/goliath/current/runtime-bundle/deploy/runpod/goliath-worker-start.sh; fi; if [[ -x /work/goliath/bin/goliath-worker-start.sh ]]; then exec bash /work/goliath/bin/goliath-worker-start.sh; fi; echo "missing goliath-worker-start.sh on /work" >&2; exit 1'
```

The stock image does not contain this repository. Before the first boot, copy the script onto the volume (the volume is persistent, so this is once per volume):

```bash
install -d -m 755 /work/goliath/bin
install -m 755 deploy/runpod/goliath-worker-start.sh /work/goliath/bin/goliath-worker-start.sh
```

Do that from a checkout while the volume is mounted, or from a one-off pod that still uses `/start.sh`. After a release bundle is promoted, the start command prefers the copy inside `/work/goliath/current`.

## Environment variables

Set plain values in the template. Reference RunPod secrets as `{{ RUNPOD_SECRET_name }}`. Do not paste secret values into the template.

| Variable | Value |
| --- | --- |
| `WORKER_API_BASE` | `https://45.76.21.8/v1` (the profile `public_base_url`; the worker appends `/workers/...`) |
| `GOLIATH_GATEWAY_CA_PEM` | `{{ RUNPOD_SECRET_GOLIATH_GATEWAY_CA_PEM }}` — PEM of the gateway `ca.pem`. Required while the gateway TLS mode is `self-signed`. |
| `GOLIATH_ENV` | `goliath-test` |
| `GOLIATH_GITHUB_REPO` | `Goliath-Research/GoliathOmics` |
| `CLUSTER_KEY` | `goliath` |
| `B2_BUCKET` | `goliath-omics` |
| `B2_ENDPOINT` | `https://s3.us-east-005.backblazeb2.com` |
| `LONGTERM_BUCKET` | `goliath-omics` |
| `LONGTERM_ENDPOINT` | `https://s3.us-east-1.myqnapcloud.io` |
| `LONGTERM_HOST` | `s3.us-east-1.myqnapcloud.io` |
| `LONGTERM_SHARE` | `goliath-omics` |
| `CURSOR_API_KEY` | `{{ RUNPOD_SECRET_CURSOR_API_KEY }}` |
| `GOLIATH_WORKER_TOKEN` | `{{ RUNPOD_SECRET_GOLIATH_WORKER_TOKEN }}` |
| `B2_ACCESS_KEY_ID` | `{{ RUNPOD_SECRET_B2_ACCESS_KEY_ID }}` |
| `B2_SECRET_ACCESS_KEY` | `{{ RUNPOD_SECRET_B2_SECRET_ACCESS_KEY }}` |
| `LONGTERM_ACCESS_KEY_ID` | `{{ RUNPOD_SECRET_LONGTERM_ACCESS_KEY_ID }}` |
| `LONGTERM_SECRET_ACCESS_KEY` | `{{ RUNPOD_SECRET_LONGTERM_SECRET_ACCESS_KEY }}` |
| `GH_TOKEN` | `{{ RUNPOD_SECRET_GH_TOKEN }}` (only needed when `/work/goliath/current` is missing) |

`NEON_TEST_PASSWORD` is not required on the worker. Set `GOLIATH_WORKER_NEEDS_DATABASE=1` and `NEON_TEST_PASSWORD={{ RUNPOD_SECRET_NEON_TEST_PASSWORD }}` only if this pod must open the database itself. The gateway holds the database password.

`GOLIATH_WORKER_CAPABILITY` is optional. Leave it unset so `methyl-worker enroll` probes the GPU host (that probe uses `nvidia-smi`). Set it only to pin a catalog capability such as `parabricks.fq2bam`. The Rollouts message always includes the GPU name `nvidia-smi` printed, plus `RUNPOD_POD_ID`, the hostname, and `arch`.

`RUNPOD_POD_ID` is injected by RunPod. Do not set it.

## Gateway certificate

goliath-test has no DNS name. The gateway deploy issues a self-signed certificate for `45.76.21.8` and keeps it at `/etc/ssl/methyl-gateway/ca.pem`. `methyl-worker` and `requests` will not trust that certificate until the pod has the PEM.

After the first gateway deploy that installs nginx:

```bash
ssh -i <deploy-key> linuxuser@45.76.21.8 sudo cat /etc/ssl/methyl-gateway/ca.pem
```

Create a RunPod secret named `GOLIATH_GATEWAY_CA_PEM` whose value is that PEM, including the `BEGIN CERTIFICATE` and `END CERTIFICATE` lines. In the template, set `GOLIATH_GATEWAY_CA_PEM` to `{{ RUNPOD_SECRET_GOLIATH_GATEWAY_CA_PEM }}`.

On boot the start script writes the PEM to `/work/goliath/env/gateway-ca.pem` (mode 644) and does not print it. When a system CA bundle is present it also writes `/work/goliath/env/requests-ca-bundle.pem` (the system bundle plus the gateway certificate) and exports `REQUESTS_CA_BUNDLE` and `SSL_CERT_FILE` to that file. `GOLIATH_GATEWAY_CA_FILE` points at the gateway certificate alone. `methyl-worker` adds `GOLIATH_GATEWAY_CA_FILE` to the default trust store for gateway HTTPS. `requests` uses `REQUESTS_CA_BUNDLE`, which still contains the system CAs, so other HTTPS calls keep working.

Restart the pod after the secret is set. A pod that was already joined still rewrites `worker.env` on boot. If you rotate the gateway certificate, delete `/etc/ssl/methyl-gateway` on the gateway, deploy again, and replace the RunPod secret with the new `ca.pem`.

Leave `GOLIATH_GATEWAY_CA_PEM` unset when `gateway.tls.mode` is `letsencrypt`. Those certificates are in the public trust store.

Optional: `GOLIATH_RELEASE_URL` is a direct tarball URL. `GOLIATH_RELEASE_TAG` selects a tag instead of the latest release. Prefer leaving both unset and letting `GH_TOKEN` read the latest `goliathomics-release-*.tar.gz` asset when `/work/goliath/current` is absent.

## What the pod does on boot

1. Wait until `/work` is mounted and writable.
2. If `/work/goliath/current` exists, use it. Otherwise download the release bundle (`GH_TOKEN` / `GITHUB_TOKEN`, or `GOLIATH_RELEASE_URL`) and run `promote_release.sh --skip-docker-pull`. If neither current nor a token/URL is available, the script exits with an error and does not start Jupyter.
3. Run `provision_worker_node.sh --join-mode join --arch amd64 --gpu` so enroll can probe GPU capability via `nvidia-smi`. Host apt setup and the VM e2e verifier are skipped: this image already has the CUDA stack, and those checks are for SSH-managed VMs.
4. Report service `goliath-worker` through `report_deploy.sh`. The message looks like `RUNPOD_POD_ID=<id> host=<hostname> arch=amd64 GPU=<nvidia-smi name>`.
5. Start `methyl-worker`. If systemd is running, install the unit. Otherwise a supervisor restarts `methyl-worker` and appends logs to `/work/goliath/logs/methyl-worker.log`.
6. `exec /start.sh`.

The script is safe to run again on the same pod. It does not print secret values.
