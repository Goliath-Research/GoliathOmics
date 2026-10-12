#!/bin/bash
# Verification for repo checkout or promoted runtime-bundle layout.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

RUNTIME_MODE=0
GOLIATH_ROOT="${GOLIATH_ROOT:-/work/goliath}"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

FAILURES=0
WARNINGS=0

usage() {
  cat <<'EOF'
Usage: scripts/verify_setup.sh [options]

Options:
  --runtime-bundle   Verify promoted release under GOLIATH_ROOT (default: /work/goliath)
  -h, --help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime-bundle) RUNTIME_MODE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

pass() { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; FAILURES=$((FAILURES + 1)); }
warn() { echo -e "${YELLOW}!${NC} $1"; WARNINGS=$((WARNINGS + 1)); }

require_file() {
  local path="$1"
  local label="${2:-$path}"
  if [[ -f "$path" ]]; then
    pass "$label"
  else
    fail "$label missing ($path)"
  fi
}

require_dir() {
  local path="$1"
  local label="${2:-$path}"
  if [[ -d "$path" ]]; then
    pass "$label"
  else
    fail "$label missing ($path)"
  fi
}

echo "============================================="
echo "MethylPipeline Verification"
echo "============================================="
echo ""

if [[ "$RUNTIME_MODE" -eq 1 ]]; then
  echo "Mode: runtime-bundle ($GOLIATH_ROOT)"
  CURRENT="$GOLIATH_ROOT/current"
  RUNTIME="$CURRENT/runtime-bundle"
  # shellcheck source=detect_platform.sh
  source "$SCRIPT_DIR/detect_platform.sh"
  ARCH="$(platform_arch_key "$(detect_uname_arch)")"
  VENV="$GOLIATH_ROOT/venv-${ARCH}"

  require_dir "$CURRENT" "current release symlink/dir"
  require_dir "$RUNTIME" "runtime-bundle"
  require_dir "$RUNTIME/domain/profiles" "domain/profiles"
  require_file "$RUNTIME/domain/profiles/samd_research.profile.json" "samd_research profile"
  require_file "$RUNTIME/domain/profiles/samd_holdout_enrichment.profile.json" "samd_holdout_enrichment profile"
  require_file "$RUNTIME/domain/profiles/samd_pivotal.profile.json" "samd_pivotal profile"
  require_dir "$RUNTIME/domain/profiles/modes" "research mode overlays"
  for mode in dmp_raw dmp_fc gene_enricher gene_fc dual_fc; do
    require_file "$RUNTIME/domain/profiles/modes/${mode}.mode.json" "mode overlay ${mode}"
  done
  require_file "$RUNTIME/domain/fixtures/mc_stability.program.json" "MC_Stability DomainProgram"
  require_file "$RUNTIME/domain/fixtures/samd_research.program.json" "SaMD_Research DomainProgram"
  require_dir "$RUNTIME/schemas" "schemas"
  require_dir "$RUNTIME/deploy" "deploy"
  require_file "$RUNTIME/deploy/env/gateway.postgres.env.example" "gateway env template"
  require_file "$RUNTIME/deploy/env/gateway.mssql.env.example" "gateway env template (mssql)"
  require_file "$RUNTIME/deploy/systemd/methyl-gateway.service" "gateway systemd unit"
  require_file "$RUNTIME/deploy/systemd/methyl-worker.service" "worker systemd unit"
  require_file "$CURRENT/manifest.json" "release manifest.json"
  require_dir "$CURRENT/wheels" "release wheels/"
  require_file "$CURRENT/requirements-worker.lock" "requirements-worker.lock"

  if [[ -x "$VENV/bin/methyl-worker" ]]; then
    pass "methyl-worker in venv-$ARCH"
  else
    fail "methyl-worker missing in $VENV (run install_release.sh)"
  fi
  if [[ -x "$VENV/bin/methyl-gateway" ]]; then
    pass "methyl-gateway in venv-$ARCH"
  else
    fail "methyl-gateway missing in $VENV (run install_release.sh)"
  fi
  if [[ -x "$VENV/bin/methyl-study-start" ]]; then
    pass "methyl-study-start in venv-$ARCH"
  else
    fail "methyl-study-start missing in $VENV"
  fi
else
  echo "Mode: repository checkout"
  echo "Project root: $PROJECT_ROOT"
  echo ""

  for dir in packages docs scripts workflow_engine workers deploy; do
    require_dir "$PROJECT_ROOT/$dir" "$dir/"
  done

  if [[ -f "$PROJECT_ROOT/scripts/packages.list" ]]; then
    pass "scripts/packages.list"
  else
    fail "scripts/packages.list missing"
  fi

  CANONICAL_DOCS=(
    "README.md"
    "docs/index.md"
    "docs/DEPLOYMENT.md"
    "docs/DOCUMENTATION_AUDIT.md"
    "docs/reference/domain-program-language.md"
    "docs/reference/admin-cli-methyl-study-start.md"
    "deploy/env/gateway.postgres.env.example"
    "deploy/env/gateway.mssql.env.example"
    "contracts/openapi.yaml"
  )
  echo ""
  echo "Checking canonical docs and deploy templates..."
  for doc in "${CANONICAL_DOCS[@]}"; do
    require_file "$PROJECT_ROOT/$doc" "$doc"
  done

  REQUIRED_SCRIPTS=(
    scripts/setup_host.sh
    scripts/install_all.sh
    scripts/bootstrap_distributed_workers.sh
    scripts/build_release.sh
    scripts/install_release.sh
    scripts/deploy_workflow_definitions.sh
    scripts/install_worker_systemd.sh
    scripts/install_gateway_systemd.sh
    scripts/provision_gateway_node.sh
    scripts/provision_worker_node.sh
    scripts/register_worker.sh
  )
  echo ""
  echo "Checking operator scripts..."
  for script in "${REQUIRED_SCRIPTS[@]}"; do
    if [[ -f "$PROJECT_ROOT/$script" ]]; then
      if [[ -x "$PROJECT_ROOT/$script" ]]; then
        pass "$script"
      else
        warn "$script exists but is not executable"
      fi
    else
      fail "$script missing"
    fi
  done

  if command -v python3 >/dev/null 2>&1; then
    pass "python3 available"
  else
    fail "python3 not found"
  fi

  if [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
    pass ".venv present"
    if "$PROJECT_ROOT/.venv/bin/python" -c "import workflow_engine" >/dev/null 2>&1; then
      pass "workflow_engine import (.venv)"
    else
      warn "workflow_engine import failed; run scripts/setup_host.sh --with-deps"
    fi
  else
    warn ".venv missing; run scripts/setup_host.sh --with-deps"
  fi
fi

echo ""
echo "============================================="
echo "Verification Complete (failures=$FAILURES warnings=$WARNINGS)"
echo "============================================="

if [[ "$FAILURES" -gt 0 ]]; then
  exit 1
fi
