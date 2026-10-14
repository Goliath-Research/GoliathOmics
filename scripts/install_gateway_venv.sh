#!/bin/bash
# Install methyl-gateway into <root>/venv-<arch> from a GoliathOmics release.
#
# methyl-gateway.service ExecStart is <root>/venv-<arch>/bin/methyl-gateway.
# Enabling that unit before this script runs makes systemd exit 203/EXEC,
# because the directory only contains env/. The console scripts live in the
# methyl-pipeline wheel shipped inside goliathomics-release-<ver>.tar.gz.
# methyl-reclaim-leases is installed from the same release.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/install_gateway_venv.sh [options]

Create <root>/venv-<arch> and install the methyl-gateway package from the
methyl-pipeline wheel in a GoliathOmics release tree. Re-running is safe:
an existing venv is reused when its Python still satisfies Requires-Python,
and pip install is repeated so a new wheel upgrades the entry points.

Options:
  --root PATH           Gateway local root (default: /opt/methyl-gateway)
  --arch KEY            aarch64 or amd64 (default: detect)
  --release PATH        Extracted goliathomics release (wheels/ or a wheel)
  --check-requires SPEC VERSION
                        Exit 0 when VERSION satisfies Requires-Python SPEC
  --select-python SPEC  Print the newest CPython uv should provision for SPEC
  -h, --help

When system python3 satisfies the wheel Requires-Python (3.14 on the gateway
VM), the venv is created with python3 -m venv. python3-venv is installed with
sudo apt when that module is missing. Otherwise uv provisions a Python that
satisfies the specifier.
EOF
}

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"

py_compat() {
  python3 - "$@" <<'PY'
import re
import sys

def release_tuple(raw):
    text = raw.strip()
    if "+" in text:
        text = text.split("+", 1)[0]
    if "!" in text:
        text = text.split("!", 1)[1]
    text = re.split(r"(?i)(?:a|b|rc|\.post|\.dev)", text, maxsplit=1)[0]
    if not re.fullmatch(r"\d+(?:\.\d+)*", text):
        raise ValueError("bad version %r" % raw)
    return tuple(int(part) for part in text.split("."))

def cmp_rel(left, right):
    width = max(len(left), len(right))
    left = left + (0,) * (width - len(left))
    right = right + (0,) * (width - len(right))
    if left < right:
        return -1
    if left > right:
        return 1
    return 0

def prefix_match(version, pattern):
    body = pattern[:-2] if pattern.endswith(".*") else pattern
    if not re.fullmatch(r"\d+(?:\.\d+)*", body):
        raise ValueError("bad version pattern %r" % pattern)
    prefix = tuple(int(part) for part in body.split("."))
    return version[: len(prefix)] == prefix

def clause_matches(clause, version):
    match = re.match(r"^(~=|==|!=|<=|>=|<|>)\s*(.+)$", clause.strip())
    if not match:
        raise ValueError("bad specifier clause %r" % clause)
    op, rhs = match.group(1), match.group(2).strip()
    if rhs.endswith(".*"):
        if op not in ("==", "!="):
            raise ValueError("wildcard is only valid with == or != (%r)" % clause)
        matched = prefix_match(version, rhs)
        return matched if op == "==" else not matched
    target = release_tuple(rhs)
    compared = cmp_rel(version, target)
    if op == "~=":
        parts = rhs.split(".")
        if len(parts) < 2:
            raise ValueError("compatible release needs two components (%r)" % clause)
        prefix = ".".join(parts[:-1]) + ".*"
        return compared >= 0 and prefix_match(version, prefix)
    return {
        ">=": compared >= 0,
        ">": compared > 0,
        "<=": compared <= 0,
        "<": compared < 0,
        "==": compared == 0,
        "!=": compared != 0,
    }[op]

def matches(spec, version):
    text = spec.strip()
    if not text:
        return True
    parsed = release_tuple(version)
    clauses = [clause.strip() for clause in text.split(",") if clause.strip()]
    return all(clause_matches(clause, parsed) for clause in clauses)

def select(spec):
    stripped = spec.strip()
    exact = re.fullmatch(r"==\s*(\d+(?:\.\d+)+)\s*", stripped)
    if exact:
        chosen = exact.group(1)
        if not matches(stripped, chosen):
            raise ValueError("exact pin does not match itself: %s" % stripped)
        print(chosen)
        return
    for minor in ("3.15", "3.14", "3.13", "3.12", "3.11", "3.10", "3.9"):
        if matches(spec, minor + ".0") or matches(spec, minor + ".99"):
            print(minor)
            return
    print("no CPython version satisfies Requires-Python: %s" % spec, file=sys.stderr)
    sys.exit(1)

def main():
    mode = sys.argv[1]
    try:
        if mode == "match":
            sys.exit(0 if matches(sys.argv[2], sys.argv[3]) else 1)
        if mode == "select":
            select(sys.argv[2])
            return
    except ValueError as exc:
        print("invalid Requires-Python: %s" % exc, file=sys.stderr)
        sys.exit(2)
    print("unknown python compat mode %s" % mode, file=sys.stderr)
    sys.exit(2)

main()
PY
}

python_matches() {
  local status=0
  set +e
  py_compat match "$1" "$2"
  status=$?
  set -e
  if [[ "$status" -eq 2 ]]; then
    die "cannot interpret Requires-Python: ${1:-<empty>}"
  fi
  return "$status"
}

python_select() {
  py_compat select "$1"
}

if [[ "${1:-}" == "--check-requires" ]]; then
  [[ $# -eq 3 ]] || die "usage: $0 --check-requires SPEC VERSION"
  if python_matches "$2" "$3"; then
    exit 0
  fi
  exit 1
fi

if [[ "${1:-}" == "--select-python" ]]; then
  [[ $# -eq 2 ]] || die "usage: $0 --select-python SPEC"
  python_select "$2"
  exit 0
fi

ROOT="${GOLIATH_ROOT:-/opt/methyl-gateway}"
ARCH=""
RELEASE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="${2:-}"; shift 2 ;;
    --arch) ARCH="${2:-}"; shift 2 ;;
    --release) RELEASE="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ "$ROOT" == /work || "$ROOT" == /work/* ]]; then
  die "Gateway --root must be this VM's local disk, not the worker /work share (got $ROOT)."
fi

ARCH="${ARCH:-$(platform_arch_key "$(detect_uname_arch)")}"
case "$ARCH" in
  aarch64|amd64) ;;
  *) die "unsupported arch: $ARCH" ;;
esac

VENV="$ROOT/venv-$ARCH"

if [[ -z "$RELEASE" ]]; then
  for candidate in "$ROOT/release" "$ROOT/current" "${GOLIATH_RELEASE_SRC:-}"; do
    if [[ -n "$candidate" && -d "$candidate" ]]; then
      RELEASE="$candidate"
      break
    fi
  done
fi

if [[ -z "$RELEASE" ]]; then
  if [[ -x "$VENV/bin/methyl-gateway" && -x "$VENV/bin/methyl-reclaim-leases" ]]; then
    info "entry points already installed in $VENV; no release directory to upgrade from"
    exit 0
  fi
  die "no release directory. Pass --release (the extracted goliathomics-release tarball) so methyl-gateway can be installed into $VENV. $VENV/bin/methyl-gateway is missing, so the service would fail with status 203/EXEC."
fi

[[ -d "$RELEASE" ]] || die "release directory not found: $RELEASE"

find_gateway_wheel() {
  python3 - "$1" <<'PY'
import sys
import zipfile
from email.parser import Parser
from pathlib import Path

release = Path(sys.argv[1])
skip = {
    "venv",
    "venv-amd64",
    "venv-aarch64",
    ".git",
    "__pycache__",
    "node_modules",
}
gateway_script = "methyl-gateway"
reclaim_script = "methyl-reclaim-leases"
gateway_names = {"methyl-gateway"}
pipeline_names = {"methyl-pipeline", "methylpipeline"}

def norm(value):
    text = value.strip().lower().replace("_", "-")
    while "--" in text:
        text = text.replace("--", "-")
    return text

def dist_info(zf, filename):
    suffix = "/" + filename
    for name in zf.namelist():
        if ".dist-info/" in name and name.endswith(suffix):
            return zf.read(name).decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    return ""

def console_scripts(text):
    found = set()
    in_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_section = line.lower() == "[console_scripts]"
            continue
        if in_section and "=" in line:
            found.add(line.split("=", 1)[0].strip())
    return found

def inspect(path):
    with zipfile.ZipFile(path) as zf:
        metadata = Parser().parsestr(dist_info(zf, "METADATA"))
        scripts = console_scripts(dist_info(zf, "entry_points.txt"))
    name = (metadata.get("Name") or "").strip()
    requires = (metadata.get("Requires-Python") or "").strip()
    return name, requires, scripts

candidates = []
wheels = [path for path in release.rglob("*.whl") if not any(part in skip for part in path.parts)]
for path in wheels:
    try:
        name, requires, scripts = inspect(path)
    except zipfile.BadZipFile:
        print("[WARN] skipping invalid wheel %s" % path, file=sys.stderr)
        continue
    normalized = norm(name)
    filename = path.name.lower()
    score = 0
    if gateway_script in scripts:
        score += 100
    if reclaim_script in scripts:
        score += 40
    if normalized in gateway_names:
        score += 20
    if normalized in pipeline_names:
        score += 10
    if "methyl" in filename and ("pipeline" in filename or "gateway" in filename):
        score += 5
    if score <= 0:
        continue
    candidates.append((score, str(path), name, requires, scripts))

if not candidates:
    print(
        "[ERROR] no methyl-gateway or methyl-pipeline wheel under %s. "
        "Expected the methyl-pipeline wheel from goliathomics-release-<ver>.tar.gz "
        "(wheels/*.whl) that provides the methyl-gateway console script." % release,
        file=sys.stderr,
    )
    sys.exit(1)

def best(rows):
    return max(rows, key=lambda item: (item[0], -len(item[1]), item[1]))

gateway_rows = [
    item for item in candidates
    if gateway_script in item[4] or norm(item[2]) in gateway_names or norm(item[2]) in pipeline_names
]
if not gateway_rows:
    gateway_rows = candidates
chosen = best(gateway_rows)
reclaim_rows = [item for item in candidates if reclaim_script in item[4]]
reclaim = best(reclaim_rows) if reclaim_rows else None
reclaim_path = ""
reclaim_requires = ""
if reclaim is not None and reclaim[1] != chosen[1]:
    reclaim_path = reclaim[1]
    reclaim_requires = reclaim[3]

print("WHEEL=%s" % chosen[1])
print("REQUIRES=%s" % chosen[3])
print("NAME=%s" % chosen[2])
print("RECLAIM_WHEEL=%s" % reclaim_path)
print("RECLAIM_REQUIRES=%s" % reclaim_requires)
PY
}

meta=""
if ! meta="$(find_gateway_wheel "$RELEASE")"; then
  die "methyl-gateway was not installed. $VENV/bin/methyl-gateway is missing, so the service would fail with status 203/EXEC."
fi

WHEEL=""
REQUIRES=""
DIST_NAME=""
RECLAIM_WHEEL=""
RECLAIM_REQUIRES=""
while IFS= read -r line || [[ -n "$line" ]]; do
  case "$line" in
    WHEEL=*) WHEEL="${line#WHEEL=}" ;;
    REQUIRES=*) REQUIRES="${line#REQUIRES=}" ;;
    NAME=*) DIST_NAME="${line#NAME=}" ;;
    RECLAIM_WHEEL=*) RECLAIM_WHEEL="${line#RECLAIM_WHEEL=}" ;;
    RECLAIM_REQUIRES=*) RECLAIM_REQUIRES="${line#RECLAIM_REQUIRES=}" ;;
  esac
done <<<"$meta"

[[ -n "$WHEEL" && -f "$WHEEL" ]] || die "wheel discovery did not return a file under $RELEASE"
info "using ${DIST_NAME:-methyl-gateway} wheel $WHEEL"
info "Requires-Python: ${REQUIRES:-unspecified}"

COMBINED="$REQUIRES"
if [[ -n "$RECLAIM_WHEEL" && -n "$RECLAIM_REQUIRES" ]]; then
  info "reclaim wheel $RECLAIM_WHEEL Requires-Python: $RECLAIM_REQUIRES"
  if [[ -n "$COMBINED" ]]; then
    COMBINED="${COMBINED},${RECLAIM_REQUIRES}"
  else
    COMBINED="$RECLAIM_REQUIRES"
  fi
fi

system_python_version() {
  python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])'
}

venv_python_version() {
  "$1/bin/python" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])'
}

venv_usable() {
  local dest="$1"
  [[ -x "$dest/bin/python" ]] || return 1
  "$dest/bin/python" -m pip --version >/dev/null 2>&1
}

install_python3_venv_pkg() {
  local py_mm
  py_mm="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  command -v sudo >/dev/null 2>&1 || die "sudo is required to install python3-venv"
  command -v apt-get >/dev/null 2>&1 || die "apt-get is required to install python3-venv"
  info "installing python3-venv (python3 -m venv is not available)"
  sudo DEBIAN_FRONTEND=noninteractive apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv
  if ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
    info "installing python${py_mm}-venv"
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "python${py_mm}-venv"
  fi
}

ensure_system_venv() {
  local dest="$1"
  local cur=""
  if venv_usable "$dest"; then
    cur="$(venv_python_version "$dest")"
    if python_matches "$COMBINED" "$cur"; then
      info "reusing venv $dest (python $cur)"
      return 0
    fi
    info "replacing $dest; python $cur does not satisfy Requires-Python: ${COMBINED:-any}"
    rm -rf "$dest"
  elif [[ -d "$dest" ]]; then
    rm -rf "$dest"
  fi
  info "creating venv $dest with python3"
  mkdir -p "$ROOT"
  if ! python3 -m venv "$dest" || ! venv_usable "$dest"; then
    rm -rf "$dest"
    install_python3_venv_pkg
    if ! python3 -m venv "$dest" || ! venv_usable "$dest"; then
      rm -rf "$dest"
      die "python3 -m venv failed after installing python3-venv; $dest is not usable"
    fi
  fi
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi
  if ! command -v curl >/dev/null 2>&1; then
    command -v sudo >/dev/null 2>&1 || die "sudo is required to install curl before uv"
    command -v apt-get >/dev/null 2>&1 || die "apt-get is required to install curl before uv"
    sudo DEBIAN_FRONTEND=noninteractive apt-get update
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates
  fi
  info "installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
  export PATH="/usr/local/bin:${HOME}/.local/bin:${PATH}"
  hash -r
  command -v uv >/dev/null 2>&1 || die "uv is not on PATH after install"
}

provision_uv_venv() {
  local ver="$1"
  local dest="$2"
  ensure_uv
  info "provisioning Python $ver with uv"
  uv python install "$ver"
  rm -rf "$dest"
  mkdir -p "$ROOT"
  if ! uv venv --python "$ver" --seed "$dest"; then
    rm -rf "$dest"
    uv venv --python "$ver" "$dest" || die "uv venv --python $ver failed for $dest"
    if ! venv_usable "$dest"; then
      uv pip install --python "$dest/bin/python" pip || die "uv could not install pip into $dest"
    fi
  fi
  venv_usable "$dest" || die "uv created $dest but $dest/bin/python -m pip does not run"
}

SYS_VER="$(system_python_version)"
if python_matches "$COMBINED" "$SYS_VER"; then
  info "system python $SYS_VER satisfies Requires-Python: ${COMBINED:-any}"
  ensure_system_venv "$VENV"
else
  info "system python $SYS_VER does not satisfy Requires-Python: ${COMBINED:-any}"
  SELECTED="$(python_select "$COMBINED")"
  [[ -n "$SELECTED" ]] || die "could not choose a Python for Requires-Python: ${COMBINED:-any}"
  if venv_usable "$VENV"; then
    cur="$(venv_python_version "$VENV")"
    if python_matches "$COMBINED" "$cur"; then
      info "reusing venv $VENV (python $cur)"
    else
      info "replacing $VENV; python $cur does not satisfy Requires-Python: ${COMBINED:-any}"
      provision_uv_venv "$SELECTED" "$VENV"
    fi
  else
    provision_uv_venv "$SELECTED" "$VENV"
  fi
fi

install_wheel() {
  local wheel="$1"
  local -a pip_args
  pip_args=(--disable-pip-version-check --upgrade)
  if [[ -d "$RELEASE/wheels" ]]; then
    pip_args+=(--find-links "$RELEASE/wheels")
  fi
  pip_args+=(--find-links "$(dirname "$wheel")")
  info "installing $wheel into $VENV"
  "$VENV/bin/python" -m pip install "${pip_args[@]}" "$wheel"
}

install_wheel "$WHEEL"
if [[ -n "$RECLAIM_WHEEL" && "$RECLAIM_WHEEL" != "$WHEEL" ]]; then
  install_wheel "$RECLAIM_WHEEL"
fi

verify_entry() {
  local name="$1"
  local path="$VENV/bin/$name"
  if [[ ! -x "$path" ]]; then
    die "$name entry point missing after install: $path (wheel $WHEEL). Refusing to enable the service; systemd would report status 203/EXEC."
  fi
  info "verified $path"
}

verify_entry methyl-gateway
verify_entry methyl-reclaim-leases
info "gateway venv ready: $VENV"
