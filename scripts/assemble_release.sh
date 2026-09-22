#!/bin/bash
# Assemble a worker release bundle from independently versioned MethylPipeline + MethylExtractor artifacts.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/assemble_release.sh [options]

Options:
  --release-version VER       Deploy bundle id (SemVer, e.g. 2026.6.1)
  --methyl-pipeline-version VER  MethylPipeline release version (SemVer)
  --methyl-extractor-version VER MethylExtractor Universal Package version (SemVer)
  --methyl-pipeline-dir PATH  Local MethylPipeline release tree (wheels/, runtime-bundle/, lockfile)
  --output DIR                Output bundle directory (default: /work/goliath/releases/<release-version>)
  --skip-methyl-extractor-download  Skip GitHub download (tarballs already in output)
  --organization URL          GitHub org URL (default: https://github.com/Goliath-Research)
  --project NAME              GitHub org slug (default: Goliath-Research)
  --feed NAME                 Ignored (GitHub Releases); kept for compatibility
  -h, --help                  Show this help

Requires methyl-pipeline-dir from MethylPipeline CI artifact (methyl-pipeline-release-<ver>).
Downloads MethylExtractor tarballs from GitHub Releases unless skipped.

Example:
  scripts/assemble_release.sh \
    --release-version 2026.6.1 \
    --methyl-pipeline-version 2026.6.1 \
    --methyl-extractor-version 2026.5.2 \
    --methyl-pipeline-dir /tmp/mp-release \
    --output /work/goliath/releases/2026.6.1
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=detect_platform.sh
source "$SCRIPT_DIR/detect_platform.sh"

RELEASE_VERSION=""
MP_VERSION=""
ME_VERSION=""
MP_DIR=""
OUTPUT=""
SKIP_ME_DL=0
ORG="${GITHUB_ORG:-https://github.com/Goliath-Research}"
PROJECT="${GITHUB_PROJECT:-Goliath-Research}"
FEED="${METHYL_EXTRACTOR_FEED:-methyl-extractor}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --release-version) RELEASE_VERSION="${2:-}"; shift 2 ;;
    --methyl-pipeline-version) MP_VERSION="${2:-}"; shift 2 ;;
    --methyl-extractor-version) ME_VERSION="${2:-}"; shift 2 ;;
    --methyl-pipeline-dir) MP_DIR="${2:-}"; shift 2 ;;
    --output) OUTPUT="${2:-}"; shift 2 ;;
    --skip-methyl-extractor-download) SKIP_ME_DL=1; shift ;;
    --organization) ORG="${2:-}"; shift 2 ;;
    --project) PROJECT="${2:-}"; shift 2 ;;
    --feed) FEED="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { echo "[INFO] $*"; }
die() { echo "[ERROR] $*" >&2; exit 1; }

[[ -n "$RELEASE_VERSION" ]] || die "--release-version is required"
[[ -n "$MP_VERSION" ]] || die "--methyl-pipeline-version is required"
[[ -n "$ME_VERSION" ]] || die "--methyl-extractor-version is required"
[[ -d "$MP_DIR" ]] || die "--methyl-pipeline-dir is required and must exist"

RELEASE_VERSION="$(normalize_release_version "$RELEASE_VERSION")"
MP_VERSION="$(normalize_release_version "$MP_VERSION")"
ME_VERSION="$(normalize_release_version "$ME_VERSION")"
require_release_version "$RELEASE_VERSION" "--release-version" || exit 1
require_release_version "$MP_VERSION" "--methyl-pipeline-version" || exit 1
require_release_version "$ME_VERSION" "--methyl-extractor-version" || exit 1

OUTPUT="${OUTPUT:-/work/goliath/releases/$RELEASE_VERSION}"
mkdir -p "$OUTPUT"

info "Assembling release bundle $RELEASE_VERSION"
info "  methyl_pipeline: $MP_VERSION"
info "  methyl_extractor: $ME_VERSION"
info "  output: $OUTPUT"

rsync_safe=(rsync -rl --delete --no-perms --no-owner --no-group --no-times)
"${rsync_safe[@]}" \
  --exclude manifest.json \
  --exclude 'methyl-extractor-linux-*.tar.gz' \
  "$MP_DIR/" "$OUTPUT/"

if [[ "$SKIP_ME_DL" -eq 0 ]]; then
  bash "$SCRIPT_DIR/download_methyl_extractor_artifacts.sh" \
    --version "$ME_VERSION" \
    --release-dir "$OUTPUT" \
    --organization "$ORG" \
    --project "$PROJECT" \
    --feed "$FEED"
fi

for arch in aarch64 amd64; do
  tb="$OUTPUT/methyl-extractor-linux-${arch}.tar.gz"
  [[ -f "$tb" ]] || die "Missing $tb"
done

python3 - "$OUTPUT" "$RELEASE_VERSION" "$MP_VERSION" "$ME_VERSION" <<'PY'
import json
import hashlib
import sys
from pathlib import Path

out = Path(sys.argv[1])
release_ver, mp_ver, me_ver = sys.argv[2:5]

stub_path = out / "manifest.json"
base = {}
if stub_path.is_file():
    base = json.loads(stub_path.read_text(encoding="utf-8"))

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

artifacts = {}
for arch in ("aarch64", "amd64"):
    name = f"methyl-extractor-linux-{arch}.tar.gz"
    path = out / name
    artifacts[arch] = {
        "methyl_extractor": name,
        "sha256": sha256_file(path),
    }

manifest = {
    "version": release_ver,
    "components": {
        "methyl_pipeline": mp_ver,
        "methyl_extractor": me_ver,
    },
    "python": base.get("python", "3.12"),
    "parabricks_image": base.get("parabricks_image", ""),
    "parabricks_image_digest": base.get("parabricks_image_digest", ""),
    "docker_data_root": base.get("docker_data_root", "/work/goliath/docker"),
    "min_driver_version": base.get("min_driver_version", ""),
    "requirements_lock": base.get("requirements_lock", "requirements-worker.lock"),
    "runtime_bundle": base.get("runtime_bundle", "runtime-bundle"),
    "artifacts": artifacts,
}

stub_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
print(f"Wrote {stub_path}")
PY

info "Assemble complete: $OUTPUT"
info "Next: run promote_release.sh or deploy pipeline with approval"
