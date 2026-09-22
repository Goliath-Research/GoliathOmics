#!/usr/bin/env bash
# Cross-analyte comparison after Plasma and Buffy MC sweeps on shared /work NFS.
#
# Example:
#   scripts/compare_analyte_sweep_results.sh --sweep-id stability_v1
#   scripts/compare_analyte_sweep_results.sh --sweep-id grid_v1 --wait --poll-seconds 60
#
# Grid sweeps (variant subdirs): compares each variant that has both analytes completed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

STUDY_ROOT="${STUDY_ROOT:-/work/projects/prostate-cancer}"
SWEEP_ROOT="${SWEEP_ROOT:-$STUDY_ROOT/sweeps}"
SWEEP_ID=""
MIN_GENE_FREQ="0.5"
WAIT=0
POLL_SECONDS=60
TIMEOUT_SECONDS=0
VARIANT=""
DRY_RUN=0

PLASMA_PROJECT_NAME="Plasma_healthy_vs_PCa"
BUFFY_PROJECT_NAME="Buffy_healthy_vs_PCa"

usage() {
  cat <<EOF
Usage: $(basename "$0") --sweep-id ID [options]

Run cross-analyte comparison tools after plasma + buffy sweeps finish.

Options:
  --sweep-id ID          Sweep under \$SWEEP_ROOT (required)
  --variant LABEL        Compare one grid variant subdir (default: sweep root + each grid variant)
  --min-gene-frequency F Gene recurrence threshold for compare_analyte_mc_gene_fc (default: 0.5)
  --wait                 Block until both analytes report state=completed (or failed)
  --poll-seconds N       Poll interval with --wait (default: 60)
  --timeout SEC          Max wait with --wait (0 = unlimited)
  --dry-run              Print compare commands only
  -h, --help             Show help

Outputs:
  \$SWEEP_ROOT/<sweep-id>/comparison/[variant/]/
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sweep-id) SWEEP_ID="${2:-}"; shift 2 ;;
    --variant) VARIANT="${2:-}"; shift 2 ;;
    --min-gene-frequency) MIN_GENE_FREQ="${2:-}"; shift 2 ;;
    --wait) WAIT=1; shift ;;
    --poll-seconds) POLL_SECONDS="${2:-}"; shift 2 ;;
    --timeout) TIMEOUT_SECONDS="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$SWEEP_ID" ]]; then
  echo "Error: --sweep-id is required." >&2
  usage
  exit 1
fi

if [[ -f "$REPO_ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.venv/bin/activate"
fi

SWEEP_DIR="$SWEEP_ROOT/$SWEEP_ID"
MANIFEST="$SWEEP_DIR/sweep_manifest.json"

state_for() {
  local analyte="$1"
  local base="$2"
  local status_file="$base/$analyte/status.json"
  if [[ ! -f "$status_file" ]]; then
    echo "missing"
    return
  fi
  python3 - <<PY "$status_file"
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("state", "unknown"))
PY
}

wait_for_both() {
  local base="$1"
  local start_ts
  start_ts="$(date +%s)"
  while true; do
    local ps bs
    ps="$(state_for plasma "$base")"
    bs="$(state_for buffy "$base")"
    echo "wait: plasma=$ps buffy=$bs ($base)"
    if [[ "$ps" == "completed" && "$bs" == "completed" ]]; then
      return 0
    fi
    if [[ "$ps" == "failed" || "$bs" == "failed" ]]; then
      echo "Error: analyte run failed (plasma=$ps buffy=$bs)." >&2
      return 1
    fi
    if [[ "$TIMEOUT_SECONDS" -gt 0 ]]; then
      local now
      now="$(date +%s)"
      if (( now - start_ts >= TIMEOUT_SECONDS )); then
        echo "Error: timeout waiting for sweep completion." >&2
        return 1
      fi
    fi
    sleep "$POLL_SECONDS"
  done
}

compare_variant() {
  local base="$1"
  local label="$2"

  local plasma_root="$base/Plasma_healthy_vs_PCa"
  local buffy_root="$base/Buffy_healthy_vs_PCa"
  local out_dir="$SWEEP_DIR/comparison"
  if [[ "$label" != "$SWEEP_ID" ]]; then
    out_dir="$out_dir/$label"
  fi

  if [[ ! -d "$plasma_root/monte_carlo_runs" || ! -d "$buffy_root/monte_carlo_runs" ]]; then
    echo "Skip $label: monte_carlo_runs missing under plasma or buffy project root." >&2
    return 0
  fi

  mkdir -p "$out_dir"

  local -a cmds=(
    "python $REPO_ROOT/tools/compare_analyte_outputs.py --plasma-root $plasma_root --buffy-root $buffy_root --comparison all/PCa --dmp-source discovery --out $out_dir/all_vs_PCa/discovery"
    "python $REPO_ROOT/tools/compare_analyte_mc_gene_fc.py --plasma-root $plasma_root --buffy-root $buffy_root --min-gene-frequency $MIN_GENE_FREQ --out $out_dir/mc_gene_fc"
  )

  if [[ -f "$plasma_root/enricher/all/PCa/ppi_hubs.csv" && -f "$buffy_root/enricher/all/PCa/ppi_hubs.csv" ]]; then
    cmds+=(
      "python $REPO_ROOT/tools/compare_analyte_mc_signatures.py --project $STUDY_ROOT/configs/project_Plasma_healthy_vs_PCa.json --plasma-hubs $plasma_root/enricher/all/PCa/ppi_hubs.csv --buffy-hubs $buffy_root/enricher/all/PCa/ppi_hubs.csv --out $out_dir/signature_recurrence/plasma"
      "python $REPO_ROOT/tools/compare_analyte_mc_signatures.py --project $STUDY_ROOT/configs/project_Buffy_healthy_vs_PCa.json --plasma-hubs $plasma_root/enricher/all/PCa/ppi_hubs.csv --buffy-hubs $buffy_root/enricher/all/PCa/ppi_hubs.csv --out $out_dir/signature_recurrence/buffy"
    )
  fi

  echo "=== Comparing variant: $label ==="
  echo "  plasma_root=$plasma_root"
  echo "  buffy_root=$buffy_root"
  echo "  out_dir=$out_dir"

  local cmd
  for cmd in "${cmds[@]}"; do
    if [[ "$DRY_RUN" == "1" ]]; then
      echo "DRY-RUN: $cmd"
    else
      eval "$cmd"
    fi
  done

  if [[ "$DRY_RUN" != "1" ]]; then
    python3 - <<PY "$out_dir" "$label" "$plasma_root" "$buffy_root" "$MIN_GENE_FREQ"
import json, sys, time
from pathlib import Path

out = Path(sys.argv[1])
label, plasma_root, buffy_root, min_freq = sys.argv[2:6]
summary_path = out / "comparison_summary.json"
payload = {
    "variant": label,
    "compared_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "plasma_root": plasma_root,
    "buffy_root": buffy_root,
    "min_gene_frequency": float(min_freq),
    "mc_gene_fc_summary": str(out / "mc_gene_fc" / "mc_gene_fc_summary.json"),
    "discovery_dmp_overlap": str(out / "all_vs_PCa" / "discovery" / "dmp_overlap_summary.json"),
}
summary_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(f"Wrote {summary_path}")
PY
  fi
}

discover_variant_bases() {
  if [[ -n "$VARIANT" ]]; then
    if [[ -d "$SWEEP_DIR/$VARIANT" ]]; then
      printf '%s\t%s\n' "$VARIANT" "$SWEEP_DIR/$VARIANT"
    else
      printf '%s\t%s\n' "$VARIANT" "$SWEEP_DIR"
    fi
    return
  fi

  # Single-variant layout: plasma/ and buffy/ directly under sweep dir.
  if [[ -d "$SWEEP_DIR/plasma" || -d "$SWEEP_DIR/buffy" ]]; then
    printf '%s\t%s\n' "$SWEEP_ID" "$SWEEP_DIR"
  fi

  # Grid layout: one subdir per variant label.
  local d
  for d in "$SWEEP_DIR"/*; do
    [[ -d "$d" ]] || continue
    local name
    name="$(basename "$d")"
    [[ "$name" == "comparison" ]] && continue
    [[ "$name" == "plasma" || "$name" == "buffy" ]] && continue
    if [[ -d "$d/plasma" || -d "$d/buffy" || -d "$d/$PLASMA_PROJECT_NAME" ]]; then
      printf '%s\t%s\n' "$name" "$d"
    fi
  done
}

main_compare() {
  local any=0
  while IFS=$'\t' read -r label base; do
    [[ -n "$label" ]] || continue
    any=1
    if [[ "$WAIT" == "1" ]]; then
      wait_for_both "$base"
    fi
    local ps bs
    ps="$(state_for plasma "$base")"
    bs="$(state_for buffy "$base")"
    if [[ "$ps" != "completed" || "$bs" != "completed" ]]; then
      echo "Skip $label: not ready (plasma=$ps buffy=$bs)." >&2
      continue
    fi
    compare_variant "$base" "$label"
  done < <(discover_variant_bases)

  if [[ "$any" -eq 0 ]]; then
    echo "Error: no variant directories found under $SWEEP_DIR" >&2
    exit 1
  fi
}

main_compare

echo "Comparison complete under $SWEEP_DIR/comparison/"
