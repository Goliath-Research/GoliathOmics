# RunPod worker template (`goliath-worker`)

`deploy/targets/goliath-test.json` does not SSH to workers. The pool boots itself from a RunPod template cloned from `runpod-torch-v280`. The container start command is one line (`deploy/runpod/template-start-command.sh`). That line downloads `deploy/runpod/goliath-worker-bootstrap.sh` from the newest non-draft `goliathomics-*` release and runs it. On an empty `/work` the bootstrap creates `/work/goliath/bin`, downloads `goliath-worker-start.sh` from that release tag, and runs it.

`goliath-worker-start.sh` waits for the `/work` volume, creates the shared layout (`samples`, `projects`, `cache`, `genomes`, `site`) and `/work/goliath/docker`, uses `/work/goliath/current` when that tree is already there, otherwise downloads the latest GitHub release bundle, runs `provision_worker_node.sh` in join mode, reports `goliath-worker` to Rollouts, starts `methyl-worker`, and execs the image's `/start.sh` so Jupyter (port 8888) and SSH (port 22) keep working.

`/work` is the network volume. It persists across pod restarts. The container disk does not. The enrollment token is copied to `/work/goliath/runpod/<RUNPOD_POD_ID>/worker-token` (mode 600) and restored to `/etc/methyl/worker-token` on the next boot. A restart of the same pod skips enroll when that token and the join stamp still match the release and `WORKER_API_BASE`.

A fresh volume does not need a manual copy of the start script. If the template line, the bootstrap, or the start script fails, the error is logged and `/start.sh` still runs, so the pod stays up for debugging instead of restart-looping.

## Clone the template

1. In the RunPod console, open **Templates** and find the template whose image is `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`. That template is `runpod-torch-v280`.
2. **Clone** it. Name the clone `goliath-worker`.
3. Leave the container image as `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`.
4. Attach a network volume and set its mount path to `/work`.
5. When you deploy a pod from this template, choose GPU **RTX 6000 Ada** × 1 (48 GB), **16 vCPU**, and **62 GB** RAM. Those match the `goliath-worker-1` pool in the target profile.

## Container start command

The cloned template's default command runs `/start.sh` directly. Replace the container start command with the one line in the block below. Paste it as one command. The line stays the same when the bootstrap or the start script changes.

`runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404` includes `curl` and `python3`. The line uses `python3` to call the GitHub API and decode the file.

`GH_TOKEN` (or `GITHUB_TOKEN`) is a fine-grained PAT with Contents read on `GOLIATH_GITHUB_REPO`. Python reads the token from the environment and sends it as an `Authorization` header. The token is not an argument of `python3` or `curl`, so the process list does not show it. The line does not print the token. Do not add `set -x`.

`GOLIATH_RELEASE_TAG` selects the git tag when it is set. Otherwise the line lists releases and uses the newest tag that starts with `goliathomics-`, skipping drafts and prereleases. It downloads `deploy/runpod/goliath-worker-bootstrap.sh` from that tag through the contents API (`GET /repos/{owner}/{repo}/contents/deploy/runpod/goliath-worker-bootstrap.sh?ref={tag}`). `gh release create` in `.github/workflows/release-assemble.yml` tags the repository checkout, and that workflow refuses to publish when this file is missing, so the path is on the tag. `GOLIATH_GITHUB_REPO` defaults to `Goliath-Research/GoliathOmics`. Leave `GOLIATH_GITHUB_API` unset; it defaults to `https://api.github.com`.

The downloaded bootstrap waits until `/work` is writable, creates `/work/goliath/{bin,tmp,logs,env}`, and then:

1. Installs `/work/goliath/current/runtime-bundle/deploy/runpod/goliath-worker-start.sh` when that file is already on the volume.
2. Otherwise runs `/work/goliath/bin/goliath-worker-start.sh` when that file is already installed.
3. Otherwise uses `GH_TOKEN` to read the GitHub release list, selects `GOLIATH_RELEASE_TAG` or the newest non-draft `goliathomics-*` tag, downloads `deploy/runpod/goliath-worker-start.sh` from that git tag through the contents API, and installs it mode 755.

The bootstrap sends its token with a mode 600 header file and removes the file. It does not print the token.

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
| `GH_TOKEN` | `{{ RUNPOD_SECRET_GH_TOKEN }}` (Contents read on `GOLIATH_GITHUB_REPO`. Required on every boot to fetch the bootstrap, to fetch the start script onto a fresh volume, and to download the release bundle when `/work/goliath/current` is missing) |

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

Optional: `GOLIATH_RELEASE_URL` is a direct tarball URL. `GOLIATH_RELEASE_TAG` selects a tag instead of the newest `goliathomics-*` release, for the template download of `goliath-worker-bootstrap.sh`, for the bootstrap download of `goliath-worker-start.sh`, and for the release bundle. Prefer leaving both unset. `GH_TOKEN` reads the newest non-draft, non-prerelease `goliathomics-*` tag and, when `/work/goliath/current` is absent, the `goliathomics-release-*.tar.gz` asset on that GitHub release.

## What the pod does on boot

1. The template command downloads `goliath-worker-bootstrap.sh` from the release tag and runs it. The bootstrap waits until `/work` is mounted and writable, installs `goliath-worker-start.sh` under `/work/goliath/bin` (from the current bundle, the existing bin copy, or the release tag), and runs it. A failure here logs and execs `/start.sh`.
2. The start script creates the shared directories when they are missing (`samples`, `projects`, `cache`, `genomes`, `site`, and `/work/goliath/docker`). When the release bundle includes `init_work_layout.sh`, that script does this and writes a default site manifest only if `/work/site/methyl_site.json` is absent.
3. If `/work/goliath/current` exists, use it. Otherwise download the release bundle (`GH_TOKEN` / `GITHUB_TOKEN`, or `GOLIATH_RELEASE_URL`) and run `promote_release.sh --skip-docker-pull`. Parabricks is not pulled on this path. If neither current nor a token/URL is available, the script logs the error and execs `/start.sh` so Jupyter and SSH stay up.
4. Run `provision_worker_node.sh --join-mode join --arch amd64 --gpu` so enroll can probe GPU capability via `nvidia-smi`. Host apt setup and the VM e2e verifier are skipped: this image already has the CUDA stack, and those checks are for SSH-managed VMs. Re-running the same pod with the same release and `WORKER_API_BASE` skips enroll.
5. Report service `goliath-worker` through `report_deploy.sh`. The message looks like `RUNPOD_POD_ID=<id> host=<hostname> arch=amd64 GPU=<nvidia-smi name>`.
6. Start `methyl-worker`. If systemd is running, install the unit. Otherwise a supervisor restarts `methyl-worker` and appends logs to `/work/goliath/logs/methyl-worker.log`.
7. `exec /start.sh`.

The script is safe to run again on the same pod. It does not print secret values. Genome files are not downloaded at boot; `genomes/` is only the shared directory (and the default site manifest, once the release bundle is present).
