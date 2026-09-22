# Shared helpers for analyte MC sweeps on /work NFS.
# Source from run_plasma_mc_sweep.sh / run_buffy_mc_sweep.sh — do not execute directly.

if [[ -z "${MC_SWEEP_REPO_ROOT:-}" ]]; then
  MC_SWEEP_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

mc_sweep_repo_root() {
  printf '%s\n' "$MC_SWEEP_REPO_ROOT"
}

mc_sweep_activate_venv() {
  local repo_root="$1"
  if [[ -f "$repo_root/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$repo_root/.venv/bin/activate"
  elif [[ -f /work/goliath/current/.venv/bin/activate ]]; then
    # shellcheck disable=SC1091
    source /work/goliath/current/.venv/bin/activate
  else
    echo "WARN: no .venv found; using PATH python." >&2
  fi
}

mc_sweep_defaults() {
  local repo_root
  repo_root="$(mc_sweep_repo_root)"
  : "${METHYL_PROFILE:=mc_dmp_gene_fc}"
  : "${METHYL_SITE_CONFIG:=/work/site/methyl_site.json}"
  if [[ -z "${METHYL_PROFILE_DIR:-}" ]]; then
    if [[ -d /work/goliath/current/runtime-bundle/domain/profiles ]]; then
      METHYL_PROFILE_DIR=/work/goliath/current/runtime-bundle/domain/profiles
    else
      METHYL_PROFILE_DIR="$repo_root/workflow_engine/domain/profiles"
    fi
  fi
  : "${STUDY_ROOT:=/work/projects/prostate-cancer}"
  : "${SWEEP_ROOT:=$STUDY_ROOT/sweeps}"
  export METHYL_PROFILE METHYL_SITE_CONFIG METHYL_PROFILE_DIR STUDY_ROOT SWEEP_ROOT
}

mc_sweep_usage_header() {
  cat <<'EOF'
Shared options (plasma and buffy runners):
  --sweep-id ID          Sweep label under /work/projects/prostate-cancer/sweeps/ID
  --params-file PATH     Validation overlay JSON (see scripts/config/analyte_sweep.example.json)
  --grid-file PATH       Run multiple overlays sequentially (scripts/config/analyte_sweep.grid.example.json)
  --output-base PATH     Override sweep output base (default: SWEEP_ROOT/SWEEP_ID)
  --resume [RUN]         Pass through to methyl-validation --resume
  --dry-run              Build configs and print commands only
  --skip-mc              Update manifest only (for testing coordination)
  -h, --help             Show help

Environment:
  METHYL_PROFILE         Pipeline profile (default: mc_dmp_gene_fc)
  METHYL_SITE_CONFIG     Site manifest (default: /work/site/methyl_site.json)
  STUDY_ROOT             Study root on NFS (default: /work/projects/prostate-cancer)
  SWEEP_ROOT             Sweeps directory (default: $STUDY_ROOT/sweeps)
EOF
}

mc_sweep_write_status() {
  local status_file="$1"
  local state="$2"
  local message="${3:-}"
  local tmp
  tmp="$(mktemp)"
  python3 - <<PY "$status_file" "$state" "$message" "$tmp"
import json, os, socket, sys, time
from pathlib import Path

out = Path(sys.argv[1])
state = sys.argv[2]
message = sys.argv[3]
tmp = Path(sys.argv[4])

payload = {
    "state": state,
    "message": message,
    "host": socket.gethostname(),
    "pid": os.getpid(),
    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
}
if out.is_file():
    try:
        prev = json.loads(out.read_text(encoding="utf-8"))
        payload["started_at"] = prev.get("started_at") or payload["updated_at"]
    except Exception:
        payload["started_at"] = payload["updated_at"]
else:
    payload["started_at"] = payload["updated_at"]

tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
  mkdir -p "$(dirname "$status_file")"
  mv "$tmp" "$status_file"
}

mc_sweep_update_manifest() {
  local manifest="$1"
  local analyte="$2"
  local sweep_id="$3"
  local state="$4"
  local project_root="$5"
  local mc_config="$6"
  local params_file="${7:-}"
  local tmp
  tmp="$(mktemp)"
  python3 - <<PY "$manifest" "$analyte" "$sweep_id" "$state" "$project_root" "$mc_config" "$params_file" "$tmp"
import json, socket, sys, time
from pathlib import Path

manifest = Path(sys.argv[1])
analyte, sweep_id, state = sys.argv[2], sys.argv[3], sys.argv[4]
project_root, mc_config = sys.argv[5], sys.argv[6]
params_file = sys.argv[7] or None
tmp = Path(sys.argv[8])

doc = {}
if manifest.is_file():
    doc = json.loads(manifest.read_text(encoding="utf-8"))

doc.setdefault("sweep_id", sweep_id)
doc.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
doc["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
doc.setdefault("analytes", {})
doc["analytes"][analyte] = {
    "state": state,
    "project_root": project_root,
    "mc_config": mc_config,
    "params_file": params_file,
    "host": socket.gethostname(),
    "updated_at": doc["updated_at"],
}

tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
PY
  mkdir -p "$(dirname "$manifest")"
  mv "$tmp" "$manifest"
}

mc_sweep_run_one() {
  local analyte="$1"
  local project_json="$2"
  local sweep_id="$3"
  local variant_label="$4"
  local output_base="$5"
  local overlay_file="$6"
  local resume_arg="$7"
  local dry_run="$8"
  local skip_mc="$9"

  local repo_root
  repo_root="$(mc_sweep_repo_root)"
  mc_sweep_activate_venv "$repo_root"
  mc_sweep_defaults

  local sweep_dir="$output_base"
  if [[ -n "$variant_label" && "$variant_label" != "$sweep_id" ]]; then
    sweep_dir="$output_base/$variant_label"
  fi

  local analyte_dir="$sweep_dir/$analyte"
  local mc_config="$analyte_dir/mc_config.json"
  local status_file="$analyte_dir/status.json"
  local manifest="$SWEEP_ROOT/$sweep_id/sweep_manifest.json"

  mkdir -p "$analyte_dir"

  mc_sweep_write_status "$status_file" "building_config" "Writing mc_config.json"

  local project_root rc=0
  project_root="$(
    python3 "$repo_root/scripts/build_mc_sweep_config.py" \
      --project "$project_json" \
      --output-base "$sweep_dir" \
      --overlay "$overlay_file" \
      --out "$mc_config" \
      --print-project-root
  )" || rc=$?
  if [[ $rc -ne 0 || -z "$project_root" || ! -f "$mc_config" ]]; then
    mc_sweep_write_status "$status_file" "failed" "build_mc_sweep_config.py failed (rc=$rc)"
    mc_sweep_update_manifest "$manifest" "$analyte" "$sweep_id" "failed" \
      "" "$mc_config" "$overlay_file"
    echo "Error: failed to build MC config for $analyte (variant=${variant_label:-<none>}, rc=$rc)." >&2
    return 1
  fi

  mc_sweep_update_manifest "$manifest" "$analyte" "$sweep_id" "configured" \
    "$project_root" "$mc_config" "$overlay_file"

  local -a cmd=(
    methyl-validation
    --config "$mc_config"
    --stability
    --stability-gene-featurecuts
  )

  if [[ -n "$resume_arg" ]]; then
    if [[ "$resume_arg" == "1" ]]; then
      cmd+=(--resume)
    else
      cmd+=(--resume "$resume_arg")
    fi
  fi

  mc_sweep_write_status "$status_file" "running" "methyl-validation --stability"
  mc_sweep_update_manifest "$manifest" "$analyte" "$sweep_id" "running" \
    "$project_root" "$mc_config" "$overlay_file"

  if [[ "$dry_run" == "1" ]]; then
    printf 'DRY-RUN [%s/%s] project_root=%s\n' "$sweep_id" "$analyte" "$project_root"
    printf '  '
    printf '%q ' "${cmd[@]}"
    printf '\n'
    return 0
  fi

  if [[ "$skip_mc" == "1" ]]; then
    mc_sweep_write_status "$status_file" "skipped" "skip-mc requested"
    return 0
  fi

  rc=0
  "${cmd[@]}" || rc=$?

  if [[ $rc -eq 0 ]]; then
    mc_sweep_write_status "$status_file" "completed" "MC stability finished"
    mc_sweep_update_manifest "$manifest" "$analyte" "$sweep_id" "completed" \
      "$project_root" "$mc_config" "$overlay_file"
    return 0
  fi

  mc_sweep_write_status "$status_file" "failed" "methyl-validation exited $rc"
  mc_sweep_update_manifest "$manifest" "$analyte" "$sweep_id" "failed" \
    "$project_root" "$mc_config" "$overlay_file"
  return "$rc"
}

mc_sweep_run_grid() {
  local analyte="$1"
  local project_json="$2"
  local sweep_id="$3"
  local output_base="$4"
  local grid_file="$5"
  local resume_arg="$6"
  local dry_run="$7"
  local skip_mc="$8"

  local repo_root grid_lines variant_count=0 rc=0
  repo_root="$(mc_sweep_repo_root)"
  mc_sweep_activate_venv "$repo_root"

  grid_lines="$(mktemp)"
  if ! python3 - <<'PY' "$grid_file" >"$grid_lines"
import json, sys
from pathlib import Path

grid = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not isinstance(grid, list):
    raise SystemExit("grid file must be a JSON array")
if not grid:
    raise SystemExit("grid file must contain at least one variant")
for i, item in enumerate(grid):
    if not isinstance(item, dict):
        raise SystemExit(f"grid[{i}] must be an object")
    label = item.get("label") or f"variant_{i+1:03d}"
    overlay = item.get("overlay") or item.get("params") or {}
    print(label)
    print(json.dumps(overlay))
PY
  then
    rm -f "$grid_lines"
    echo "Error: invalid or empty grid file: $grid_file" >&2
    return 1
  fi

  while IFS= read -r variant_label; do
    IFS= read -r overlay_json || {
      rm -f "$grid_lines"
      echo "Error: malformed grid variant record for label: ${variant_label:-<unknown>}" >&2
      return 1
    }
    variant_count=$((variant_count + 1))
    local overlay_file
    overlay_file="$(mktemp)"
    printf '%s\n' "$overlay_json" >"$overlay_file"
    rc=0
    mc_sweep_run_one "$analyte" "$project_json" "$sweep_id" "$variant_label" \
      "$output_base" "$overlay_file" "$resume_arg" "$dry_run" "$skip_mc" || rc=$?
    rm -f "$overlay_file"
    if [[ $rc -ne 0 ]]; then
      rm -f "$grid_lines"
      return "$rc"
    fi
  done <"$grid_lines"
  rm -f "$grid_lines"

  if [[ $variant_count -eq 0 ]]; then
    echo "Error: grid file produced no variants: $grid_file" >&2
    return 1
  fi

  return 0
}
