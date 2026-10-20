#!/usr/bin/env bash
# Refuse to start a worker when local scratch cannot hold a real WGBS sample.
#
# Workers stage FASTQ.gz files (larger than 25 GB each), BAMs, Parabricks tmp,
# logs, venvs, and docker layers on container-local disk. /work is the shared
# mount (geesefs on RunPod) and must not be used for scratch.
#
#   METHYL_LOCAL_TMP          scratch root (default /tmp/methyl-pb)
#   METHYL_LOCAL_TMP_MIN_GB   free-space floor in GB (default 60; 1024-based,
#                             the same unit as `df -BG`). Create refuses a
#                             container disk under 150 GB so this floor still
#                             holds after MethylExtractor, GoliathAlign, and
#                             the venv are installed. Start runs this check
#                             before those installs.
#   GOLIATH_WORK_MOUNT        shared mount to reject (default /work)
#   METHYL_LOCAL_STALE_HOURS  boot sweep: per-sample BAM / extract dirs under
#                             ${METHYL_LOCAL_TMP}/bam and /extract with no file
#                             touched in this many hours are deleted
#                             (default 72; 0 disables). Their pinned tasks
#                             cannot resume anyway; the sample restarts from Align.
#
# On success, prints one line to stdout and creates the scratch root.
# On failure, prints one [local-scratch] ERROR line to stderr and exits 1.

set +x
set -euo pipefail

WORK_MOUNT="${GOLIATH_WORK_MOUNT:-/work}"
SCRATCH="${METHYL_LOCAL_TMP:-/tmp/methyl-pb}"
MIN_GB="${METHYL_LOCAL_TMP_MIN_GB:-60}"
STALE_HOURS="${METHYL_LOCAL_STALE_HOURS:-72}"

die() { printf '[local-scratch] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$MIN_GB" =~ ^[0-9]+$ ]] || die "METHYL_LOCAL_TMP_MIN_GB must be a non-negative integer (got '$MIN_GB')"
[[ "$STALE_HOURS" =~ ^[0-9]+$ ]] || die "METHYL_LOCAL_STALE_HOURS must be a non-negative integer (got '$STALE_HOURS')"
[[ "$SCRATCH" == /* ]] || die "METHYL_LOCAL_TMP must be an absolute path (got '$SCRATCH')"
[[ "$WORK_MOUNT" == /* ]] || die "GOLIATH_WORK_MOUNT must be an absolute path (got '$WORK_MOUNT')"

strip_slash() {
  local p="${1%/}"
  [[ -n "$p" ]] || p=/
  printf '%s' "$p"
}

# Canonical path. GNU readlink -f resolves the existing prefix even when the
# final component does not exist yet, so a symlink into the work mount is
# rejected before it is created.
canon() {
  readlink -f -- "$1" 2>/dev/null || strip_slash "$1"
}

work_real="$(canon "$(strip_slash "$WORK_MOUNT")")"
scratch_real="$(canon "$(strip_slash "$SCRATCH")")"
if [[ "$scratch_real" == "$work_real" || "$scratch_real" == "$work_real"/* ]]; then
  die "METHYL_LOCAL_TMP=${SCRATCH} is on the shared work mount ${WORK_MOUNT}. Local scratch must be container-local disk (default /tmp/methyl-pb). ${WORK_MOUNT} is shared storage and must not be used for scratch."
fi

# Boot sweep of stale worker-local BAMs / extract outputs before measuring.
sweep_stale() {
  local sub dir
  (( STALE_HOURS > 0 )) || return 0
  for sub in bam extract; do
    [[ -d "$scratch_real/$sub" ]] || continue
    for dir in "$scratch_real/$sub"/*/; do
      [[ -d "$dir" ]] || continue
      # Keep a directory while anything inside changed recently.
      if [[ -z "$(find "$dir" -mmin "-$((STALE_HOURS * 60))" -print -quit 2>/dev/null)" ]]; then
        rm -rf -- "$dir" && printf '[local-scratch] removed stale %s\n' "${dir%/}" >&2
      fi
    done
  done
}
sweep_stale

# Measure the filesystem that will hold the scratch root. df needs an existing
# path, so walk up to the first ancestor before the directory is created.
probe="$scratch_real"
while [[ ! -e "$probe" && "$probe" != / ]]; do
  probe="$(dirname "$probe")"
done
[[ -e "$probe" ]] || die "cannot locate a filesystem for METHYL_LOCAL_TMP=${SCRATCH}"

free_bytes() {
  local path="$1" bytes
  # -P and --output cannot be combined. -B1 is 1-byte blocks; field 4 is available.
  bytes="$(df -PB1 "$path" 2>/dev/null | awk 'NR==2 {print $4}')"
  [[ "$bytes" =~ ^[0-9]+$ ]] || return 1
  printf '%s' "$bytes"
}

gb_text() {
  awk -v bytes="$1" 'BEGIN { printf "%.1f", bytes / 1073741824 }'
}

min_bytes=$((MIN_GB * 1024 * 1024 * 1024))
refuse_if_below() {
  local bytes="$1"
  [[ "$bytes" -ge "$min_bytes" ]] && return 0
  die "local scratch ${SCRATCH} has $(gb_text "$bytes") GB free, below the minimum of ${MIN_GB} GB (METHYL_LOCAL_TMP_MIN_GB). Workers stage FASTQ.gz files larger than 25 GB, BAMs, Parabricks tmp, logs, venvs, and docker layers on this disk. ${WORK_MOUNT} is shared storage and is not scratch. Raise the container disk (worker profile local_disk_gb, default 200, minimum 150) or free space on ${SCRATCH}."
}

avail="$(free_bytes "$probe")" || die "could not read free space for ${SCRATCH} (df ${probe})"
refuse_if_below "$avail"

mkdir -p -- "$SCRATCH" || die "cannot create local scratch ${SCRATCH}"
chmod 1777 "$SCRATCH" 2>/dev/null || true
[[ -w "$SCRATCH" ]] || die "local scratch ${SCRATCH} is not writable"

avail="$(free_bytes "$SCRATCH")" || die "could not read free space for ${SCRATCH}"
refuse_if_below "$avail"

printf 'local scratch %s has %s GB free (minimum %s GB)\n' "$SCRATCH" "$(gb_text "$avail")" "$MIN_GB"
