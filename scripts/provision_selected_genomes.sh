#!/usr/bin/env bash
# Phase 0 helper: ensure site-pinned genomes exist under /work/genomes.
#
# Prefer local files when already present. Otherwise sync from
# goliath/genomes/ (myQNAPcloud) via methyl-cfg provision-assets or
# scripts/sync_genomes_to_s3.sh --download.
#
# Usage:
#   export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...
#   scripts/provision_selected_genomes.sh
#   scripts/provision_selected_genomes.sh --dry-run
#   scripts/provision_selected_genomes.sh --force-sync
#
# Env:
#   WORK_ROOT=/work
#   METHYL_SITE_CONFIG=/work/site/methyl_site.json
#   METHYL_CFG_STORE=/work/goliath/cfg-store

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK_ROOT="${WORK_ROOT:-/work}"
SITE_JSON="${METHYL_SITE_CONFIG:-${WORK_ROOT}/site/methyl_site.json}"
DRY_RUN=0
FORCE=0

usage() {
  cat <<'EOF'
Usage: scripts/provision_selected_genomes.sh [--dry-run] [--force-sync]

Verify site-pinned genome paths under WORK_ROOT/genomes. If missing (or
--force-sync), download from goliath-genomes via methyl-cfg or aws sync.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --force-sync) FORCE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown arg: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ! -f "$SITE_JSON" ]]; then
  EXAMPLE="$ROOT/tools/methyl-config-editor/configs/site_grch38.example.json"
  if [[ -f "$EXAMPLE" ]]; then
    echo "Site missing at $SITE_JSON — installing example"
    mkdir -p "$(dirname "$SITE_JSON")"
    if [[ "$DRY_RUN" -eq 0 ]]; then
      cp "$EXAMPLE" "$SITE_JSON"
    fi
  else
    echo "ERROR: site manifest not found: $SITE_JSON" >&2
    exit 1
  fi
fi

# shellcheck disable=SC1091
source "${ROOT}/.venv/bin/activate" 2>/dev/null || true

export WORK_ROOT
export METHYL_SITE_CONFIG="$SITE_JSON"
export PYTHONPATH="${ROOT}/workflow_engine${PYTHONPATH:+:$PYTHONPATH}"

check_paths() {
  python - <<'PY'
import json, os, sys
from pathlib import Path

work = Path(os.environ.get("WORK_ROOT", "/work"))
site_path = Path(os.environ["METHYL_SITE_CONFIG"])
doc = json.loads(site_path.read_text(encoding="utf-8"))
sys.path.insert(0, os.environ.get("PYTHONPATH", "").split(os.pathsep)[0])
from cfg.reference_selection import verify_selected_paths

res = verify_selected_paths(doc, work_root=work)
print("OK" if res["ok"] else "MISSING")
for m in res["missing"]:
    print(m)
sys.exit(0 if res["ok"] else 1)
PY
}

echo "Checking selected genome paths (site=$SITE_JSON)..."
if [[ "$FORCE" -eq 0 ]] && check_paths; then
  echo "Selected genomes already present under ${WORK_ROOT}/genomes — nothing to do."
  exit 0
fi

echo "Provisioning from goliath/genomes/ ..."

if command -v methyl-cfg >/dev/null 2>&1; then
  STORE_ARGS=()
  if [[ -n "${METHYL_CFG_STORE:-}" ]]; then
    STORE_ARGS=(--store-dir "$METHYL_CFG_STORE")
  fi
  methyl-cfg import-fs --repo-root "$ROOT" "${STORE_ARGS[@]}" || true
  if [[ -f "$SITE_JSON" ]]; then
    methyl-cfg import-fs --repo-root "$ROOT" --work-root "$WORK_ROOT" "${STORE_ARGS[@]}" || true
  fi
  ARGS=(provision-assets --selected-only --site default --work-root "$WORK_ROOT")
  if [[ "$DRY_RUN" -eq 1 ]]; then
    ARGS+=(--dry-run)
  fi
  methyl-cfg "${ARGS[@]}" "${STORE_ARGS[@]}"
else
  echo "methyl-cfg not on PATH — falling back to scripts/sync_genomes_to_s3.sh --download"
  SYNC_ARGS=(--download)
  if [[ "$DRY_RUN" -eq 1 ]]; then
    SYNC_ARGS+=(--dry-run)
  fi
  bash "$ROOT/scripts/sync_genomes_to_s3.sh" "${SYNC_ARGS[@]}"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Dry-run complete."
  exit 0
fi

echo "Re-checking selected paths..."
if check_paths; then
  echo "Done — selected genomes ready."
  exit 0
fi
echo "ERROR: selected genome paths still missing after provision" >&2
echo "Hint: scripts/sync_genomes_to_s3.sh --download --only linear/GRCh38/ensembl-116" >&2
exit 1
