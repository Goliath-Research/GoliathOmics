#!/bin/bash
# Install methyl-worker systemd unit on this VM (local daemon, shared /work env).
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/install_worker_systemd.sh [options]

Options:
  --root PATH           GoliathOmics root (default: /work/goliath)
  --arch KEY            aarch64 or amd64 (default: detect)
  --capability NAME     Install methyl-worker@NAME.service instead of omnibus
  --detect-capabilities Install one unit per auto-detected capability (or omnibus for '*')
  --runtime PATH        Path to runtime-bundle (default: <root>/current/runtime-bundle)
  --no-start            Install/enable only; do not start
  -h, --help
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"
# shellcheck source=rollouts_hook.sh
source "$SCRIPT_DIR/rollouts_hook.sh"

ROOT="${GOLIATH_ROOT:-/work/goliath}"
ARCH=""
CAPABILITY=""
DETECT_CAPABILITIES=0
RUNTIME=""
NO_START=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --arch) ARCH="${2:-}"; shift 2 ;;
    --capability) CAPABILITY="${2:-}"; shift 2 ;;
    --detect-capabilities) DETECT_CAPABILITIES=1; shift ;;
    --runtime) RUNTIME="${2:-}"; shift 2 ;;
    --no-start) NO_START=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash $0 ..." >&2
  exit 1
fi

ARCH="${ARCH:-$(platform_arch_key "$(detect_uname_arch)")}"
_rollouts_host="$(hostname -s 2>/dev/null || hostname)"
rollouts_begin goliath-worker "host=${_rollouts_host} arch=${ARCH} capability=${CAPABILITY:-unspecified}"
VENV="$ROOT/venv-${ARCH}"
RUNTIME="${RUNTIME:-$(readlink -f "$ROOT/current/runtime-bundle" 2>/dev/null || echo "$SCRIPT_DIR/..")}"
DEPLOY="$RUNTIME/deploy/systemd"
[[ -d "$DEPLOY" ]] || DEPLOY="$SCRIPT_DIR/../deploy/systemd"

WORKER_BIN="$VENV/bin/methyl-worker"
[[ -x "$WORKER_BIN" ]] || WORKER_BIN="$ROOT/venv/bin/methyl-worker"

# shellcheck disable=SC2034 # rendered units set PATH; kept from upstream
PATH_LINE="$VENV/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

SCRATCH="${METHYL_SAMTOOLS_TMPDIR:-/var/tmp/methyl-samtools}"
mkdir -p "$SCRATCH"
chmod 1777 "$SCRATCH" 2>/dev/null || true

# systemd treats % as a specifier and does not unquote a leading - path.
# Node ids and the private dir are single path tokens; reject a path that
# would split the unit line.
_systemd_environment_file() {
  local path="$1"
  case "$path" in
    *[[:space:]]*|*'#'*)
      echo "EnvironmentFile path must be one token: $path" >&2
      return 1
      ;;
  esac
  path="${path//%/%%}"
  printf 'EnvironmentFile=-%s' "$path"
}

_render_unit() {
  local src="$1" dest="$2"
  local node="${GOLIATH_NODE_WORKER_ENV:-}"
  local secrets="${GOLIATH_SECRETS_ENV:-}"
  local rendered line out node_line="" secrets_line=""
  rendered="$(mktemp)"
  out="$(mktemp)"
  sed -e "s|__GOLIATH_ROOT__|$ROOT|g" \
      -e "s|__GOLIATH_VENV__|$VENV|g" \
      "$src" >"$rendered"
  if [[ -n "$node" ]]; then
    if ! node_line="$(_systemd_environment_file "$node")"; then
      rm -f "$rendered" "$out"
      return 1
    fi
  fi
  if [[ -n "$secrets" ]]; then
    if ! secrets_line="$(_systemd_environment_file "$secrets")"; then
      rm -f "$rendered" "$out"
      return 1
    fi
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      EnvironmentFile=-__GOLIATH_NODE_ENV__)
        [[ -n "$node_line" ]] && printf '%s\n' "$node_line" >>"$out"
        ;;
      EnvironmentFile=-__GOLIATH_SECRETS_ENV__)
        [[ -n "$secrets_line" ]] && printf '%s\n' "$secrets_line" >>"$out"
        ;;
      *)
        printf '%s\n' "$line" >>"$out"
        ;;
    esac
  done <"$rendered"
  mv -f "$out" "$dest"
  rm -f "$rendered"
}

_detect_capabilities() {
  local py="$VENV/bin/python"
  [[ -x "$py" ]] || py="python3"
  "$py" -c "import json; from methyl_worker.capabilities import resolve_worker_capabilities; print(json.dumps(resolve_worker_capabilities()))"
}

_install_template_unit() {
  local src="$DEPLOY/methyl-worker@.service"
  [[ -f "$src" ]] || { echo "Missing $src" >&2; exit 1; }
  _render_unit "$src" "/etc/systemd/system/methyl-worker@.service"
}

if [[ "$DETECT_CAPABILITIES" -eq 1 && -z "$CAPABILITY" ]]; then
  mapfile -t CAPS < <(_detect_capabilities | python3 -c "import json,sys; print('\n'.join(json.load(sys.stdin)))")
  if [[ "${#CAPS[@]}" -eq 0 ]]; then
    echo "No capabilities detected" >&2
    exit 1
  fi
  if [[ "${#CAPS[@]}" -eq 1 && "${CAPS[0]}" == "*" ]]; then
    CAPABILITY=""
  else
    rollouts_set_message "host=${_rollouts_host} arch=${ARCH} capability=$(IFS=,; echo "${CAPS[*]}")"
    _install_template_unit
    systemctl daemon-reload
    for cap in "${CAPS[@]}"; do
      [[ -z "$cap" || "$cap" == "*" ]] && continue
      UNIT="methyl-worker@${cap}.service"
      systemctl enable "$UNIT"
      [[ "$NO_START" -eq 0 ]] && systemctl restart "$UNIT"
      systemctl status "$UNIT" --no-pager -l -n 8
    done
    exit 0
  fi
fi

if [[ -n "$CAPABILITY" ]]; then
  rollouts_set_message "host=${_rollouts_host} arch=${ARCH} capability=${CAPABILITY}"
  UNIT="methyl-worker@${CAPABILITY}.service"
  SRC="$DEPLOY/methyl-worker@.service"
  [[ -f "$SRC" ]] || { echo "Missing $SRC" >&2; exit 1; }
  _render_unit "$SRC" "/etc/systemd/system/methyl-worker@.service"
  systemctl daemon-reload
  systemctl enable "$UNIT"
  [[ "$NO_START" -eq 0 ]] && systemctl restart "$UNIT"
  systemctl status "$UNIT" --no-pager -l -n 15
  exit 0
fi

SRC="$DEPLOY/methyl-worker.service"
[[ -f "$SRC" ]] || { echo "Missing $SRC" >&2; exit 1; }

_render_unit "$SRC" "/etc/systemd/system/methyl-worker.service"

if [[ "$DETECT_CAPABILITIES" -eq 1 && -z "$CAPABILITY" ]]; then
  rollouts_set_message "host=${_rollouts_host} arch=${ARCH} capability=*"
else
  rollouts_set_message "host=${_rollouts_host} arch=${ARCH} capability=${CAPABILITY:-unspecified}"
fi
systemctl daemon-reload
systemctl enable methyl-worker.service
[[ "$NO_START" -eq 0 ]] && systemctl restart methyl-worker.service
systemctl status methyl-worker.service --no-pager -l -n 15
