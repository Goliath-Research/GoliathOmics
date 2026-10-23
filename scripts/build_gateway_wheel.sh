#!/bin/bash
# Build the core methyl-gateway wheel into a release wheels/ directory.
#
# methyl-gateway and methyl-reclaim-leases are console scripts of the
# workflow engine distribution (project name methyl-gateway). That project
# lives in GoliathApp workflow_engine/pyproject.toml. This repo has the
# reclaim and admin modules (workflow_engine/ops, workflow_engine/admin)
# and no pyproject, so a packages.list build of the science plugins never
# produced the wheel. The wheel version is --version (the release tag),
# not a fixed release number.
#
# --sql-out bundles GoliathApp's workflow_engine/sql_pg scripts from the same
# checkout (scripts/bundle_goliathapp_sql.py), so the release carries the
# database definition that matches its gateway wheel.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/build_gateway_wheel.sh --version VER --wheels DIR [options]

Options:
  --version VER     Release version (SemVer). Stamped into the wheel.
  --wheels DIR      Destination wheels/ directory
  --source DIR      workflow_engine tree to build (must contain pyproject.toml).
                    Default: clone GoliathApp (GOLIATH_APP_URL / GOLIATH_APP_REF).
  --sha-file PATH   Write the gateway source revision (git SHA, or local-source)
  --sql-out DIR     Also bundle <source>/sql_pg (GoliathApp PostgreSQL scripts,
                    apply_schema.sh order) into DIR
  -h, --help
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

VERSION=""
WHEELS=""
SOURCE=""
SHA_FILE=""
SQL_OUT=""
PYTHON_BIN="${PYTHON_BIN:-python3}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="${2:-}"; shift 2 ;;
    --wheels) WHEELS="${2:-}"; shift 2 ;;
    --source) SOURCE="${2:-}"; shift 2 ;;
    --sha-file) SHA_FILE="${2:-}"; shift 2 ;;
    --sql-out) SQL_OUT="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

[[ -n "$VERSION" ]] || die "--version is required"
[[ -n "$WHEELS" ]] || die "--wheels is required"
VERSION="$(normalize_release_version "$VERSION")"
require_release_version "$VERSION" "--version" || exit 1
mkdir -p "$WHEELS"

STAGE="$(mktemp -d)"
CLONE=""
cleanup() {
  rm -rf "$STAGE"
  if [[ -n "$CLONE" ]]; then
    rm -rf "$CLONE"
  fi
}
trap cleanup EXIT

fetch_goliath_app() {
  local dest="$1"
  local url="${GOLIATH_APP_URL:-https://github.com/Goliath-Research/GoliathApp.git}"
  local ref="${GOLIATH_APP_REF:-main}"
  info "fetching methyl-gateway sources from ${url} (${ref})"
  if [[ "$ref" =~ ^[0-9a-fA-F]{40}$ ]]; then
    git init -q "$dest"
    git -C "$dest" remote add origin "$url"
    git -C "$dest" fetch --depth 1 origin "$ref"
    git -C "$dest" checkout --detach FETCH_HEAD
  else
    git clone --depth 1 --branch "$ref" "$url" "$dest"
  fi
  [[ -f "$dest/workflow_engine/pyproject.toml" ]] || die "GoliathApp checkout has no workflow_engine/pyproject.toml"
}

SOURCE_SHA=""
SQL_SRC=""
if [[ -n "$SOURCE" ]]; then
  [[ -f "$SOURCE/pyproject.toml" ]] || die "--source has no pyproject.toml: $SOURCE"
  if git -C "$SOURCE" rev-parse HEAD >/dev/null 2>&1; then
    SOURCE_SHA="$(git -C "$SOURCE" rev-parse HEAD)"
  else
    SOURCE_SHA="local-source"
  fi
  info "building methyl-gateway wheel from $SOURCE ($SOURCE_SHA)"
  SQL_SRC="$SOURCE/sql_pg"
  cp -R "$SOURCE/." "$STAGE/"
elif [[ -f "$REPO_ROOT/workflow_engine/pyproject.toml" ]]; then
  SOURCE_SHA="local-source"
  if git -C "$REPO_ROOT" rev-parse HEAD >/dev/null 2>&1; then
    SOURCE_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  fi
  info "building methyl-gateway wheel from $REPO_ROOT/workflow_engine ($SOURCE_SHA)"
  SQL_SRC="$REPO_ROOT/workflow_engine/sql_pg"
  cp -R "$REPO_ROOT/workflow_engine/." "$STAGE/"
else
  CLONE="$(mktemp -d)"
  fetch_goliath_app "$CLONE"
  SOURCE_SHA="$(git -C "$CLONE" rev-parse HEAD)"
  info "gateway source revision $SOURCE_SHA"
  SQL_SRC="$CLONE/workflow_engine/sql_pg"
  cp -R "$CLONE/workflow_engine/." "$STAGE/"
fi

if [[ -n "$SQL_OUT" ]]; then
  [[ -f "$SQL_SRC/apply_schema.sh" ]] || die "no GoliathApp sql_pg/apply_schema.sh under $SQL_SRC; cannot bundle the PostgreSQL scripts"
  python3 "$SCRIPT_DIR/bundle_goliathapp_sql.py" \
    --sql-dir "$SQL_SRC" \
    --out "$SQL_OUT" \
    --commit "$SOURCE_SHA" \
    --repository "${GOLIATH_APP_URL:-https://github.com/Goliath-Research/GoliathApp.git}"
fi

# Reclaim and study CLIs live in this repo. GoliathApp declares the entry
# points and ships rest/; ops/ and admin/ are overlaid so methyl-reclaim-leases
# imports. Copy onto the stage only — the checkout and this repo stay untouched.
for name in ops admin; do
  if [[ -d "$REPO_ROOT/workflow_engine/$name" ]]; then
    mkdir -p "$STAGE/$name"
    cp -R "$REPO_ROOT/workflow_engine/$name/." "$STAGE/$name/"
  fi
done

python3 - "$STAGE/pyproject.toml" "$VERSION" "$STAGE" <<'PY'
import re
import sys
from pathlib import Path

path, version, root = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]*", version):
    print("[ERROR] refusing to stamp wheel version %r" % version, file=sys.stderr)
    sys.exit(1)
text = path.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)
out = []
in_project = False
replaced = False
for line in lines:
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        in_project = stripped == "[project]"
    if in_project and not replaced and re.match(r"version\s*=", stripped):
        newline = "\n" if line.endswith("\n") else ""
        out.append('version = "%s"%s' % (version, newline))
        replaced = True
        continue
    if in_project and re.match(r"readme\s*=", stripped):
        match = re.search(r'readme\s*=\s*"([^"]+)"', stripped)
        if match is None:
            match = re.search(r"readme\s*=\s*'([^']+)'", stripped)
        readme = root / match.group(1) if match else None
        if readme is None or not readme.is_file():
            continue
    out.append(line)
if not replaced:
    print("[ERROR] %s has no [project] version" % path, file=sys.stderr)
    sys.exit(1)
path.write_text("".join(out), encoding="utf-8")
PY

info "building methyl-gateway wheel version $VERSION into $WHEELS"
"$PYTHON_BIN" -m pip wheel "$STAGE" -w "$WHEELS" --no-deps

if [[ -n "$SHA_FILE" ]]; then
  printf '%s\n' "$SOURCE_SHA" >"$SHA_FILE"
fi

info "gateway source: $SOURCE_SHA"
