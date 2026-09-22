#!/usr/bin/env bash
# Install PCa1-5 check bundle to /work/projects/prostate-cancer (cluster layout).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$ROOT/../../../.." && pwd)"
source "$REPO_ROOT/.venv/bin/activate"
exec python "$ROOT/check_pipeline.py" --install "$@"
