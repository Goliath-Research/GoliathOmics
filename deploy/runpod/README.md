# RunPod worker template (`goliath-worker`)

`deploy/targets/goliath-test.json` does not SSH to workers. The pool boots itself from a RunPod template cloned from `runpod-torch-v280`. The container start command is the bootstrap below (`deploy/runpod/goliath-worker-bootstrap.sh`). On an empty `/work` it creates `/work/goliath/bin`, downloads `goliath-worker-start.sh` from the `goliathomics-*` release tag, and runs it.

`goliath-worker-start.sh` waits for the `/work` volume, creates the shared layout (`samples`, `projects`, `cache`, `genomes`, `site`) and `/work/goliath/docker`, uses `/work/goliath/current` when that tree is already there, otherwise downloads the latest GitHub release bundle, runs `provision_worker_node.sh` in join mode, reports `goliath-worker` to Rollouts, starts `methyl-worker`, and execs the image's `/start.sh` so Jupyter (port 8888) and SSH (port 22) keep working.

`/work` is the network volume. It persists across pod restarts. The container disk does not. The enrollment token is copied to `/work/goliath/runpod/<RUNPOD_POD_ID>/worker-token` (mode 600) and restored to `/etc/methyl/worker-token` on the next boot. A restart of the same pod skips enroll when that token and the join stamp still match the release and `WORKER_API_BASE`.

A fresh volume does not need a manual copy of the start script. If bootstrap or the start script fails, the error is logged and `/start.sh` still runs, so the pod stays up for debugging instead of restart-looping.

## Clone the template

1. In the RunPod console, open **Templates** and find the template whose image is `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`. That template is `runpod-torch-v280`.
2. **Clone** it. Name the clone `goliath-worker`.
3. Leave the container image as `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404`.
4. Attach a network volume and set its mount path to `/work`.
5. When you deploy a pod from this template, choose GPU **RTX 6000 Ada** × 1 (48 GB), **16 vCPU**, and **62 GB** RAM. Those match the `goliath-worker-1` pool in the target profile.

## Container start command

The cloned template's default command runs `/start.sh` directly. Replace the container start command with the text in the block below. Paste it as one command. It is `bash -lc` of `deploy/runpod/goliath-worker-bootstrap.sh`.

The command waits until `/work` is writable, creates `/work/goliath/{bin,tmp,logs,env}`, and then:

1. Installs `/work/goliath/current/runtime-bundle/deploy/runpod/goliath-worker-start.sh` when that file is already on the volume.
2. Otherwise runs `/work/goliath/bin/goliath-worker-start.sh` when that file is already installed.
3. Otherwise uses `GH_TOKEN` to read the GitHub release list, selects `GOLIATH_RELEASE_TAG` or the newest non-draft `goliathomics-*` tag, downloads `deploy/runpod/goliath-worker-start.sh` from that git tag through the contents API, and installs it mode 755.

The token is sent with a mode 600 header file and the file is removed. The command does not print the token. Do not add `set -x`.

If a step fails, or the start script exits non-zero, the command logs the error and execs `/start.sh`. sshd on port 22 and Jupyter on port 8888 stay up, and the container does not exit, so RunPod does not restart-loop. A stop signal still ends the pod.

<!-- goliath-bootstrap-command:begin -->
```text
bash -lc '
#!/usr/bin/env bash
# Fetch goliath-worker-start.sh onto an empty /work volume and run it.
#
# The RunPod template start command is bash -lc of this file.
# This file must not contain a single-quote character, because that command
# wraps the file in single quotes. Do not run it under set -x.
#
# GH_TOKEN (or GITHUB_TOKEN) is a fine-grained PAT with Contents read. It is
# written to a mode 600 header file and removed. It is never printed.
# GOLIATH_RELEASE_TAG pins the git tag. Unset, the newest non-draft
# goliathomics-* release is used. That tag is exported for the start
# script release-bundle download.
# On failure the pod stays up: /start.sh keeps sshd (port 22) and Jupyter
# (port 8888) running so RunPod does not restart the container.
# GOLIATH_START_NO_EXEC=1 makes failures exit 1 instead (tests).

set +x
set -euo pipefail
set -E

WORK_MOUNT="${GOLIATH_WORK_MOUNT:-/work}"
ROOT="${GOLIATH_ROOT:-$WORK_MOUNT/goliath}"
WAIT_ATTEMPTS="${GOLIATH_WORK_WAIT_ATTEMPTS:-90}"
ORIG_UMASK="$(umask)"
HEADER_FILE=""
DOWNLOAD_TMP=""
META_FILE=""
RELEASE_TAG=""
child=0

log() { printf "[goliath-bootstrap] %s\n" "$*"; }

hold() {
  umask "$ORIG_UMASK"
  rm -f "$HEADER_FILE" "$DOWNLOAD_TMP" "$META_FILE"
  HEADER_FILE=""
  DOWNLOAD_TMP=""
  META_FILE=""
  printf "[goliath-bootstrap] ERROR: %s\n" "$*" >&2
  if [[ "${GOLIATH_START_NO_EXEC:-}" == "1" ]]; then
    exit 1
  fi
  printf "[goliath-bootstrap] keeping the pod alive for debugging (sshd port 22, Jupyter port 8888)\n" >&2
  local keepalive="${GOLIATH_START_KEEPALIVE:-/start.sh}"
  if [[ -x "$keepalive" ]]; then
    exec "$keepalive"
  fi
  printf "[goliath-bootstrap] %s is not executable; sleeping so the pod is not restarted\n" "$keepalive" >&2
  exec sleep infinity
}

on_stop() {
  if [[ "$child" != "0" ]]; then
    kill -TERM "$child" 2>/dev/null || true
  fi
  rm -f "$HEADER_FILE" "$DOWNLOAD_TMP" "$META_FILE"
  exit 0
}
on_err() {
  local rc=$?
  local line="$1"
  trap - ERR
  hold "command failed at line ${line} (exit ${rc})"
}
trap on_stop TERM INT
trap "on_err \"\${BASH_LINENO[0]}\"" ERR

wait_for_work() {
  local i
  for ((i = 1; i <= WAIT_ATTEMPTS; i++)); do
    if [[ -d "$WORK_MOUNT" && -w "$WORK_MOUNT" ]]; then
      return 0
    fi
    sleep 2
  done
  hold "persistent volume ${WORK_MOUNT} is not mounted and writable"
}

script_ok() {
  local path="$1"
  [[ -f "$path" && -s "$path" ]] || return 1
  head -n 1 "$path" | grep -q "bash" || return 1
  grep -q "goliath-worker-start" "$path"
}

require_repo() {
  local repo="$1"
  if [[ ! "$repo" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
    hold "GOLIATH_GITHUB_REPO must look like owner/name"
  fi
}

require_tag() {
  local tag="$1"
  if [[ ! "$tag" =~ ^goliathomics-[A-Za-z0-9._+-]+$ ]]; then
    hold "release tag must look like goliathomics-<version>; set GOLIATH_RELEASE_TAG or publish a goliathomics release"
  fi
}

curl_auth() {
  local url="$1"
  local dest="$2"
  local accept="$3"
  curl -fsSL --retry 3 --retry-delay 2 \
    -H "@${HEADER_FILE}" \
    -H "Accept: ${accept}" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    -o "$dest" \
    "$url"
}

resolve_tag() {
  local repo="$1"
  RELEASE_TAG="${GOLIATH_RELEASE_TAG:-}"
  if [[ -n "$RELEASE_TAG" ]]; then
    return 0
  fi
  META_FILE="$ROOT/tmp/github-releases.json"
  local api="https://api.github.com/repos/${repo}/releases?per_page=100"
  curl_auth "$api" "$META_FILE" "application/vnd.github+json" \
    || hold "could not list GitHub releases"
  RELEASE_TAG="$(python3 - "$META_FILE" <<PY
import json, sys
raw = json.load(open(sys.argv[1], encoding="utf-8"))
if isinstance(raw, dict):
    raw = [raw]
if not isinstance(raw, list):
    sys.exit(0)
for rel in raw:
    if not isinstance(rel, dict):
        continue
    if rel.get("draft") or rel.get("prerelease"):
        continue
    name = rel.get("tag_name") or ""
    if name.startswith("goliathomics-"):
        sys.stdout.write(name)
        break
PY
)" || {
    rm -f "$META_FILE"
    META_FILE=""
    return 1
  }
  rm -f "$META_FILE"
  META_FILE=""
  [[ -n "$RELEASE_TAG" ]] || return 1
}

decode_contents() {
  python3 - "$META_FILE" >"$DOWNLOAD_TMP" <<PY
import base64, json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
if not isinstance(doc, dict):
    sys.stderr.write("contents response was not an object\n")
    sys.exit(1)
encoding = doc.get("encoding") or ""
data = doc.get("content") or ""
if encoding != "base64" or not data:
    sys.stderr.write("contents response has no base64 content\n")
    sys.exit(1)
sys.stdout.buffer.write(base64.b64decode(data))
PY
}

fetch_start_script() {
  local dest="$1"
  local token="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
  local repo="${GOLIATH_GITHUB_REPO:-Goliath-Research/GoliathOmics}"
  local old_umask url
  [[ -n "$token" ]] || hold "GH_TOKEN is unset and ${dest} is not on the volume"
  case "$token" in
    *"
"*) hold "GH_TOKEN contains a newline" ;;
  esac
  require_repo "$repo"
  command -v python3 >/dev/null 2>&1 || hold "python3 is required to read the GitHub response"
  command -v curl >/dev/null 2>&1 || hold "curl is required to download goliath-worker-start.sh"
  old_umask="$(umask)"
  umask 077
  HEADER_FILE="$ROOT/tmp/github.headers"
  printf "Authorization: Bearer %s\n" "$token" >"$HEADER_FILE"
  chmod 600 "$HEADER_FILE"
  resolve_tag "$repo" || hold "no goliathomics GitHub release; set GOLIATH_RELEASE_TAG"
  require_tag "$RELEASE_TAG"
  export GOLIATH_RELEASE_TAG="$RELEASE_TAG"
  log "downloading goliath-worker-start.sh from ${repo} ref ${RELEASE_TAG}"
  url="https://api.github.com/repos/${repo}/contents/deploy/runpod/goliath-worker-start.sh?ref=${RELEASE_TAG}"
  META_FILE="$ROOT/tmp/goliath-worker-start.json"
  DOWNLOAD_TMP="$ROOT/tmp/goliath-worker-start.download"
  curl_auth "$url" "$META_FILE" "application/vnd.github+json" \
    || hold "could not download goliath-worker-start.sh at ${RELEASE_TAG}"
  decode_contents || hold "GitHub contents response did not include the start script"
  rm -f "$META_FILE" "$HEADER_FILE"
  META_FILE=""
  HEADER_FILE=""
  umask "$old_umask"
  script_ok "$DOWNLOAD_TMP" || hold "downloaded file is not goliath-worker-start.sh"
  install -m 755 "$DOWNLOAD_TMP" "$dest"
  rm -f "$DOWNLOAD_TMP"
  DOWNLOAD_TMP=""
  log "installed ${dest} from ${RELEASE_TAG}"
}

install_start_script() {
  local bundled="$ROOT/current/runtime-bundle/deploy/runpod/goliath-worker-start.sh"
  local dest="$ROOT/bin/goliath-worker-start.sh"
  if script_ok "$bundled"; then
    install -m 755 "$bundled" "$dest"
    log "installed ${dest} from the current release bundle"
  elif script_ok "$dest"; then
    chmod 755 "$dest" || hold "cannot chmod ${dest}"
    log "using existing ${dest}"
  else
    fetch_start_script "$dest"
  fi
  script_ok "$dest" || hold "${dest} is missing after install"
}

run_start() {
  local dest="$1"
  local rc=0
  shift
  log "starting ${dest}"
  bash "$dest" "$@" &
  child=$!
  set +e
  wait "$child"
  rc=$?
  set -e
  child=0
  if [[ "$rc" -ne 0 ]]; then
    hold "goliath-worker-start.sh exited ${rc}"
  fi
  exit 0
}

main() {
  wait_for_work
  install -d -m 755 "$ROOT" "$ROOT/bin" "$ROOT/tmp" "$ROOT/logs" "$ROOT/env" \
    || hold "cannot create ${ROOT}"
  install_start_script
  run_start "$ROOT/bin/goliath-worker-start.sh" "$@"
}

main "$@"
'
```
<!-- goliath-bootstrap-command:end -->

`GH_TOKEN` is required the first time the volume has neither the bundle copy nor `/work/goliath/bin/goliath-worker-start.sh`. After a release bundle is promoted, the next boot prefers the copy inside `/work/goliath/current`.

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
| `GH_TOKEN` | `{{ RUNPOD_SECRET_GH_TOKEN }}` (Contents read on `GOLIATH_GITHUB_REPO`. Required to fetch the start script onto a fresh volume, and to download the release bundle when `/work/goliath/current` is missing) |

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

Optional: `GOLIATH_RELEASE_URL` is a direct tarball URL. `GOLIATH_RELEASE_TAG` selects a tag instead of the newest `goliathomics-*` release, both for the bootstrap download of `goliath-worker-start.sh` and for the release bundle. Prefer leaving both unset. `GH_TOKEN` reads the newest `goliathomics-*` tag and, when `/work/goliath/current` is absent, downloads the `goliathomics-release-*.tar.gz` asset from that same release.

## What the pod does on boot

1. The template command waits until `/work` is mounted and writable, installs `goliath-worker-start.sh` under `/work/goliath/bin` (from the current bundle, the existing bin copy, or the release tag), and runs it. A failure here logs and execs `/start.sh`.
2. The start script creates the shared directories when they are missing (`samples`, `projects`, `cache`, `genomes`, `site`, and `/work/goliath/docker`). When the release bundle includes `init_work_layout.sh`, that script does this and writes a default site manifest only if `/work/site/methyl_site.json` is absent.
3. If `/work/goliath/current` exists, use it. Otherwise download the release bundle (`GH_TOKEN` / `GITHUB_TOKEN`, or `GOLIATH_RELEASE_URL`) and run `promote_release.sh --skip-docker-pull`. Parabricks is not pulled on this path. If neither current nor a token/URL is available, the script logs the error and execs `/start.sh` so Jupyter and SSH stay up.
4. Run `provision_worker_node.sh --join-mode join --arch amd64 --gpu` so enroll can probe GPU capability via `nvidia-smi`. Host apt setup and the VM e2e verifier are skipped: this image already has the CUDA stack, and those checks are for SSH-managed VMs. Re-running the same pod with the same release and `WORKER_API_BASE` skips enroll.
5. Report service `goliath-worker` through `report_deploy.sh`. The message looks like `RUNPOD_POD_ID=<id> host=<hostname> arch=amd64 GPU=<nvidia-smi name>`.
6. Start `methyl-worker`. If systemd is running, install the unit. Otherwise a supervisor restarts `methyl-worker` and appends logs to `/work/goliath/logs/methyl-worker.log`.
7. `exec /start.sh`.

The script is safe to run again on the same pod. It does not print secret values. Genome files are not downloaded at boot; `genomes/` is only the shared directory (and the default site manifest, once the release bundle is present).
