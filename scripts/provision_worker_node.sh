#!/bin/bash
# Provision a GPU worker VM: first seeds shared /work, joiners enroll then install VM-local.
# Database bootstrap is a privileged-host step — this script never runs DDL or SQL register.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/provision_worker_node.sh [options]

Join modes:
  --join-mode auto      Default. Missing /work/goliath/current/manifest.json → first;
                        present → join.
  --join-mode join      VM-local host/Docker (skip Parabricks pull), then enroll.
  --join-mode first     Seed shared /work (layout, release, extractor, one Parabricks pull),
                        then enroll, then systemd.

Staged Arc:
  --prepare-only        Host tools + Docker/CTK + shared seed (first) or skip enroll
  --finish-enroll       Enroll + systemd (requires live WORKER_API_BASE)

Options:
  --root PATH              GoliathOmics root on /work (default: /work/goliath)
  --release-dir PATH       Release dir for first-worker promote (default: <root>/current)
  --arch KEY               aarch64 or amd64
  --cluster KEY            wf.cluster cluster_key (default: goliath)
  --gpu                    Pass --gpu to bootstrap/setup_host; require nvidia-smi
  --join-mode MODE         auto (default) | join | first
  --skip-promote           Alias for --join-mode join
  --prepare-only
  --finish-enroll
  --arc-onboard            Run install_arc_agent.sh
  --require-arc            Verify Arc Connected before enroll
  --enroll-worker          Enroll via methyl-worker enroll (implied by --finish-enroll)
  --register-worker        Rejected: production enroll is gateway-only
  --enable-systemd         Install methyl-worker units
  --capability NAME
  --detect-capabilities
  --skip-preflight
  --skip-host-setup        Join mode only: do not run setup_host.sh / bootstrap
  --skip-verify            Do not run verify_e2e_node.sh
  --dry-run
  -h, --help

RunPod pods pass --skip-host-setup and --skip-verify. The base image already
has the CUDA stack, and systemd may be absent. See deploy/runpod/README.md.

Env: WORKER_API_BASE (required for enroll).
The portal must preregister this VM (cluster, worker key, public IP) before enroll.
Enroll is refused until that row exists; this script does not insert it.

Local scratch: METHYL_LOCAL_TMP (default /tmp/methyl-pb) must have
METHYL_LOCAL_TMP_MIN_GB free (default 60). A smaller disk refuses to start.
The shared work mount is not scratch. --skip-preflight does not skip this check.
Create's minimum container disk is 150 GB, so this floor still holds after
MethylExtractor, GoliathAlign, and the venv are installed.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"
# shellcheck source=worker_venv_local.sh
source "$SCRIPT_DIR/worker_venv_local.sh"
# shellcheck source=rollouts_hook.sh
source "$SCRIPT_DIR/rollouts_hook.sh"

ROOT="${GOLIATH_ROOT:-/work/goliath}"
RELEASE_DIR=""
ARCH=""
CLUSTER="${CLUSTER_KEY:-goliath}"
GPU=0
JOIN_MODE="auto"
ARC_ONBOARD=0
REQUIRE_ARC=0
REGISTER=0
ENABLE_SYSTEMD=0
PREPARE_ONLY=0
FINISH_ENROLL=0
CAPABILITY=""
DETECT_CAPABILITIES=0
SKIP_PREFLIGHT=0
SKIP_HOST_SETUP=0
SKIP_VERIFY=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --release-dir) RELEASE_DIR="${2:-}"; shift 2 ;;
    --arch) ARCH="${2:-}"; shift 2 ;;
    --cluster) CLUSTER="${2:-}"; shift 2 ;;
    --gpu) GPU=1; shift ;;
    --join-mode)
      JOIN_MODE="${2:-}"
      shift 2
      ;;
    --skip-promote) JOIN_MODE="join"; shift ;;
    --prepare-only) PREPARE_ONLY=1; shift ;;
    --finish-enroll) FINISH_ENROLL=1; shift ;;
    --arc-onboard) ARC_ONBOARD=1; shift ;;
    --require-arc) REQUIRE_ARC=1; shift ;;
    --enroll-worker) REGISTER=1; shift ;;
    --register-worker)
      echo "Direct-DB register is not allowed on GPU workers." >&2
      echo "Portal-preregister this VM, then methyl-worker enroll (WORKER_API_BASE)." >&2
      exit 2
      ;;
    --enable-systemd) ENABLE_SYSTEMD=1; shift ;;
    --capability) CAPABILITY="${2:-}"; shift 2 ;;
    --detect-capabilities) DETECT_CAPABILITIES=1; shift ;;
    --skip-preflight) SKIP_PREFLIGHT=1; shift ;;
    --skip-host-setup) SKIP_HOST_SETUP=1; shift ;;
    --skip-verify) SKIP_VERIFY=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

run() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[DRY-RUN] $*"
  else
    "$@"
  fi
}

die() { echo "[ERROR] $*" >&2; exit 1; }

# Create the configured temp directories on local disk before anything calls
# mktemp. worker.env sets TMPDIR=/var/tmp/methyl-samtools (samtools sort
# scratch); a fresh container does not have it and mktemp then fails. A
# directory that cannot be created makes TMPDIR fall back to /tmp.
ensure_tmpdirs() {
  local d envfile cfg=""
  for envfile in "${ROOT:-/work/goliath}/env/worker.env" "${GOLIATH_POD_ENV_FILE:-}"; do
    [[ -n "$envfile" && -f "$envfile" ]] || continue
    cfg="$(sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}TMPDIR=//p' "$envfile" 2>/dev/null | tail -n 1 | tr -d '"'"'"'')"
    [[ -n "$cfg" ]] && break
  done
  for d in "${TMPDIR:-}" "$cfg" "${METHYL_SAMTOOLS_TMPDIR:-/var/tmp/methyl-samtools}"; do
    [[ -n "$d" && "$d" == /* ]] || continue
    if mkdir -p "$d" 2>/dev/null && [[ -w "$d" ]]; then
      chmod 1777 "$d" 2>/dev/null || true
    else
      echo "[WARN] cannot create temp dir ${d}" >&2
      if [[ "$d" == "${TMPDIR:-}" ]]; then
        echo "[WARN] TMPDIR=${d} is unusable; using /tmp" >&2
        TMPDIR=/tmp
        export TMPDIR
      fi
    fi
  done
}
ensure_tmpdirs

case "$JOIN_MODE" in
  auto|join|first) ;;
  *) die "Invalid --join-mode: $JOIN_MODE (use auto|join|first)" ;;
esac

if [[ "$PREPARE_ONLY" -eq 1 && "$FINISH_ENROLL" -eq 1 ]]; then
  die "Use either --prepare-only or --finish-enroll, not both"
fi

if [[ "$FINISH_ENROLL" -eq 1 ]]; then
  REQUIRE_ARC=1
  REGISTER=1
  ENABLE_SYSTEMD=1
fi

ARCH="${ARCH:-$(platform_arch_key "$(detect_uname_arch)")}"
CURRENT_DIR="$(goliath_current_dir "$ROOT" 2>/dev/null || true)"
RELEASE_DIR="${RELEASE_DIR:-${CURRENT_DIR:-$ROOT/current}}"
MANIFEST="${CURRENT_DIR:-$ROOT/current}/manifest.json"

if [[ "$JOIN_MODE" == "auto" ]]; then
  if [[ -f "$MANIFEST" ]]; then
    JOIN_MODE="join"
  else
    JOIN_MODE="first"
  fi
  echo "Auto-detected join-mode=$JOIN_MODE (manifest $([[ -f "$MANIFEST" ]] && echo present || echo missing))"
fi

RUNTIME="$(readlink -f "$RELEASE_DIR/runtime-bundle" 2>/dev/null || true)"
[[ -d "${RUNTIME:-}" ]] || RUNTIME="$(readlink -f "${CURRENT_DIR:-$ROOT/current}/runtime-bundle" 2>/dev/null || true)"
[[ -d "${RUNTIME:-}" ]] || RUNTIME="$REPO_ROOT"
SCRIPTS="$RUNTIME/scripts"
[[ -d "$SCRIPTS" ]] || SCRIPTS="$SCRIPT_DIR"

# Always, including --skip-preflight: a worker that cannot stage a FASTQ on
# local disk must not enroll. --dry-run prints the command and does not check.
require_local_scratch() {
  local scratch_check="$SCRIPTS/check_local_scratch.sh"
  [[ -f "$scratch_check" ]] || scratch_check="$SCRIPT_DIR/check_local_scratch.sh"
  [[ -f "$scratch_check" ]] || die "check_local_scratch.sh not found; refusing to provision without a local scratch preflight"
  echo "=== Local scratch (${METHYL_LOCAL_TMP:-/tmp/methyl-pb}, minimum ${METHYL_LOCAL_TMP_MIN_GB:-60} GB) ==="
  run bash "$scratch_check"
}
require_local_scratch

resolve_venv_python() {
  local cand
  # Pod-local venv only; nothing on the shared volume can execute.
  cand="$(worker_venv_local_dir "$ARCH")/bin/python"
  if [[ -x "$cand" ]]; then
    echo "$cand"
    return 0
  fi
  return 1
}

do_enroll() {
  echo "=== Enroll worker (gateway) ==="
  # Capture before sourcing shared worker.env — write_worker_env.sh may have
  # omitted WORKER_API_BASE or left a seed-time value that must not override
  # the live export on this VM.
  local live_api_base="${WORKER_API_BASE:-${METHYL_API_BASE:-}}"
  [[ -n "$live_api_base" ]] || die "WORKER_API_BASE (or METHYL_API_BASE) is required — no SQL register on GPU VMs"
  local venv_py=""
  venv_py="$(resolve_venv_python)" || die "Worker venv python not found under $(worker_venv_local_dir "$ARCH") (run promote_release.sh or worker_venv_local.sh --release <release> first)"
  local worker_key
  worker_key="$(hostname -s)"
  echo "Enroll requires a portal preregistration for cluster ${CLUSTER}, key ${worker_key}, and this VM's public IP."
  echo "Create that row in the portal first (portal.sp_upsert_worker_enrollment). This script will not insert it."
  [[ "$REQUIRE_ARC" -eq 1 ]] && run bash "$SCRIPTS/verify_arc_prereqs.sh"
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/env/worker.env" 2>/dev/null || true
  # shellcheck disable=SC1091
  source "$ROOT/env/parabricks.env" 2>/dev/null || true
  set +a
  # worker.env just exported TMPDIR (/var/tmp/methyl-samtools); create it
  # before the mktemp below and every enroll attempt.
  ensure_tmpdirs
  export WORKER_API_BASE="$live_api_base"
  local enroll_args=(enroll --api-base "$live_api_base" --cluster "$CLUSTER" --key "$worker_key")
  if [[ -n "$CAPABILITY" ]]; then
    enroll_args+=(--capabilities-json "[\"$CAPABILITY\"]")
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    run "$venv_py" -m methyl_worker "${enroll_args[@]}"
    return 0
  fi
  # A missing portal preregistration is expected on a fresh pod: wait for
  # the operator instead of exiting. GOLIATH_ENROLL_RETRY_SECS (default 60)
  # sets the interval, GOLIATH_ENROLL_MAX_WAIT_SECS (default 0 = unlimited)
  # the limit. Any other enroll error still fails.
  local retry="${GOLIATH_ENROLL_RETRY_SECS:-60}" max_wait="${GOLIATH_ENROLL_MAX_WAIT_SECS:-0}"
  local out waited=0 announced=0 rc
  [[ "$retry" =~ ^[0-9]+$ && "$retry" -ge 1 ]] || retry=60
  [[ "$max_wait" =~ ^[0-9]+$ ]] || max_wait=0
  out="$(mktemp)"
  while :; do
    set +e
    env PYTHONPATH="${REPO_ROOT}/workers${PYTHONPATH:+:$PYTHONPATH}" \
      WORKER_API_BASE="$live_api_base" \
      "$venv_py" -m methyl_worker "${enroll_args[@]}" >"$out" 2>&1
    rc=$?
    set -e
    cat "$out"
    if [[ "$rc" -eq 0 ]]; then
      [[ "$announced" -eq 1 ]] && echo "[enroll] portal registration found after ${waited}s; enrolled"
      rm -f "$out"
      return 0
    fi
    if ! grep -qi 'not preregistered' "$out"; then
      rm -f "$out"
      die "methyl-worker enroll failed (exit ${rc})"
    fi
    if [[ "$announced" -eq 0 ]]; then
      local pub_ip
      pub_ip="$(enroll_public_ip)"
      echo "[enroll] waiting for portal registration: cluster=${CLUSTER} key=${worker_key} public_ip=${pub_ip}"
      enroll_registration_sql "$CLUSTER" "$pub_ip" "$worker_key"
      announced=1
    fi
    if [[ "$max_wait" -gt 0 && "$waited" -ge "$max_wait" ]]; then
      rm -f "$out"
      die "[enroll] no portal registration after ${waited}s (GOLIATH_ENROLL_MAX_WAIT_SECS=${max_wait})"
    fi
    sleep "$retry"
    waited=$((waited + retry))
  done
}

# Ready-to-run preregistration for an operator (no secrets: connection
# details come from the operator's own environment). The proc takes only
# cluster_key, public_ip and external_worker_key; arch/GPU/node are context.
enroll_registration_sql() {
  local c k ip
  c="${1//\'/\'\'}"; ip="${2//\'/\'\'}"; k="${3//\'/\'\'}"
  local gpu
  gpu="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n 1 || true)"
  echo "[enroll] context: node=${GOLIATH_NODE_ID:-${RUNPOD_POD_ID:-$(hostname -s 2>/dev/null || hostname)}} arch=${ARCH:-?} capability=${CAPABILITY:-unspecified} gpu=${gpu:-unknown}"
  echo "[enroll] preregister with (PostgreSQL/Neon):"
  echo "  psql \"\$DATABASE_URL\" -c \"SELECT * FROM portal.sp_upsert_worker_enrollment('${c}', '${ip}', '${k}');\""
  echo "[enroll] or (Azure SQL): EXEC portal.sp_upsert_worker_enrollment @cluster_key = N'${c}', @public_ip = N'${ip}', @external_worker_key = N'${k}';"
  echo "[enroll] retrying every ${GOLIATH_ENROLL_RETRY_SECS:-60}s"
}

# Public IP as the gateway sees it, for the waiting message only.
enroll_public_ip() {
  local ip="${GOLIATH_PUBLIC_IP:-${RUNPOD_PUBLIC_IP:-}}"
  if [[ -z "$ip" ]] && command -v curl >/dev/null 2>&1; then
    # env -u: a private LD_LIBRARY_PATH entry must not shadow the system libcurl.
    ip="$(env -u LD_LIBRARY_PATH -u LD_PRELOAD curl -fsS --max-time 5 "${GOLIATH_PUBLIC_IP_URL:-https://api.ipify.org}" 2>/dev/null || true)"
  fi
  [[ "$ip" =~ ^[0-9a-fA-F.:]+$ ]] || ip="unknown"
  printf '%s' "$ip"
}

do_systemd() {
  echo "=== systemd ==="
  local sd_args=(--root "$ROOT" --arch "$ARCH" --runtime "$RUNTIME")
  local host msg rc
  host="$(hostname -s 2>/dev/null || hostname)"
  msg="host=${host} arch=${ARCH} capability=${CAPABILITY:-unspecified}"
  if [[ -n "$CAPABILITY" ]]; then
    sd_args+=(--capability "$CAPABILITY")
  elif [[ "$DETECT_CAPABILITIES" -eq 1 ]]; then
    sd_args+=(--detect-capabilities)
  fi
  if [[ "$DRY_RUN" -eq 0 ]]; then
    rollouts_report start --service goliath-worker --message "$msg"
  fi
  set +e
  run sudo env GOLIATH_ROLLOUTS_EXTERNAL=1 ROLLOUTS_MESSAGE="$msg" bash "$SCRIPTS/install_worker_systemd.sh" "${sd_args[@]}"
  rc=$?
  set -e
  if [[ "$DRY_RUN" -eq 0 ]]; then
    if [[ "$rc" -eq 0 ]]; then
      rollouts_report succeeded --service goliath-worker --message "$msg"
    else
      rollouts_report failed --service goliath-worker --message "$msg (exit ${rc})"
    fi
  fi
  return "$rc"
}

do_host_and_docker() {
  local skip_pb="$1"
  echo "=== Host deps ==="
  local host_args=(--system-deps --no-venv)
  [[ "$GPU" -eq 1 ]] && host_args+=(--gpu)
  run bash "$SCRIPTS/setup_host.sh" "${host_args[@]}"

  echo "=== MethylPipeline bundle (join-mode=$JOIN_MODE) ==="
  local boot_args=(--root "$ROOT" --arch "$ARCH" --release-dir "$RELEASE_DIR")
  [[ "$GPU" -eq 1 ]] && boot_args+=(--gpu)
  if [[ "$PREPARE_ONLY" -eq 0 && "$REQUIRE_ARC" -eq 1 ]]; then
    boot_args+=(--require-arc)
  fi
  if [[ "$skip_pb" -eq 1 ]]; then
    boot_args+=(--skip-parabricks-pull)
  else
    boot_args+=(--promote-release)
  fi
  [[ -n "${WORKER_API_BASE:-}" ]] && export WORKER_API_BASE
  run bash "$SCRIPTS/bootstrap_goliath.sh" "${boot_args[@]}"
}

ensure_work_layout() {
  local work_root
  work_root="$(dirname "$ROOT")"
  if [[ -x "$SCRIPTS/init_work_layout.sh" ]]; then
    run bash "$SCRIPTS/init_work_layout.sh" --work "$work_root"
  fi
}

do_shared_seed() {
  echo "=== Seed shared /work (first worker) ==="
  local work_root
  work_root="$(dirname "$ROOT")"
  ensure_work_layout
  [[ -d "$RELEASE_DIR" ]] || die "Release directory not found: $RELEASE_DIR (assemble/promote artifacts onto /work first)"
  do_host_and_docker 0
  if [[ ! -d "$work_root/genomes" || ! -d "$work_root/site" ]]; then
    echo "NOTE: $work_root/genomes or $work_root/site missing after layout seed."
    echo "      Download references onto the share (methyl-cfg provision-assets / scripts/provision_selected_genomes.sh)."
  fi
}

if [[ "$FINISH_ENROLL" -eq 1 ]]; then
  if [[ "$SKIP_PREFLIGHT" -eq 0 ]]; then
    local_pf=(--root "$ROOT" --require-current --require-api)
    [[ "$GPU" -eq 1 ]] && local_pf+=(--gpu)
    run bash "$SCRIPTS/preflight_worker_join.sh" "${local_pf[@]}"
  fi
  run bash "$SCRIPTS/verify_arc_prereqs.sh"
  do_enroll
  do_systemd
  echo "Finish-enroll complete."
  exit 0
fi

if [[ "$JOIN_MODE" == "first" ]]; then
  echo "=== Shared /work layout (first worker) ==="
  ensure_work_layout
fi

if [[ "$SKIP_PREFLIGHT" -eq 0 ]]; then
  echo "=== Preflight ==="
  pf_args=(--root "$ROOT")
  if [[ "$JOIN_MODE" == "join" ]]; then
    pf_args+=(--require-current)
  else
    pf_args+=(--allow-missing-current)
  fi
  [[ "$GPU" -eq 1 ]] && pf_args+=(--gpu)
  if [[ "$REGISTER" -eq 1 || "$JOIN_MODE" == "join" ]] && [[ "$PREPARE_ONLY" -eq 0 ]]; then
    pf_args+=(--require-api)
  fi
  run bash "$SCRIPTS/preflight_worker_join.sh" "${pf_args[@]}"
fi

if [[ "$PREPARE_ONLY" -eq 0 && "$ARC_ONBOARD" -eq 1 ]]; then
  echo "=== Azure Arc ==="
  run sudo bash "$SCRIPTS/install_arc_agent.sh" \
    --subscription-id "${AZ_SUBSCRIPTION_ID:-}" \
    --resource-group "${AZ_RESOURCE_GROUP:-}" \
    --tenant-id "${AZURE_TENANT_ID:-}" \
    ${AZ_ARC_LOCATION:+--location "$AZ_ARC_LOCATION"}
fi

if [[ "$PREPARE_ONLY" -eq 0 && "$REQUIRE_ARC" -eq 1 ]]; then
  run bash "$SCRIPTS/verify_arc_prereqs.sh"
fi

if [[ "$JOIN_MODE" == "join" ]]; then
  # Host tools (samtools/bedtools/docker) must exist before enroll probes
  # resolve_worker_capabilities; the gateway persists that set.
  # RunPod images already provide the GPU stack and have no VM apt path.
  if [[ "$SKIP_HOST_SETUP" -eq 0 ]]; then
    do_host_and_docker 1
  else
    echo "Skipping host and Docker setup (--skip-host-setup)"
  fi
  if [[ "$PREPARE_ONLY" -eq 0 ]]; then
    REGISTER=1
    do_enroll
  fi
else
  do_shared_seed
  if [[ "$PREPARE_ONLY" -eq 0 ]]; then
    REGISTER=1
    do_enroll
  fi
fi

if [[ "$SKIP_VERIFY" -eq 0 ]]; then
  echo "=== Verify ==="
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/env/worker.env" 2>/dev/null || true
  # shellcheck disable=SC1091
  source "$ROOT/env/parabricks.env" 2>/dev/null || true
  set +a
  run bash "$SCRIPTS/verify_e2e_node.sh"
else
  echo "Skipping verify_e2e_node.sh (--skip-verify)"
fi

if [[ "$PREPARE_ONLY" -eq 1 ]]; then
  echo "Prepare-only complete."
  if [[ "$JOIN_MODE" == "first" ]]; then
    echo "Shared /work seeded. Start the gateway if needed, then --finish-enroll."
  else
    echo "Next: enroll with WORKER_API_BASE set: $0 --join-mode join --finish-enroll --cluster $CLUSTER"
  fi
  exit 0
fi

if [[ "$ENABLE_SYSTEMD" -eq 1 ]]; then
  do_systemd
fi

echo "Provision complete (join-mode=$JOIN_MODE)."
