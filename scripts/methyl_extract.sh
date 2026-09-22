#!/bin/bash
# Local dev / manual HPC wrapper for MethylExtractor sample extraction.
#
# Usage:
#   methyl_extract.sh SAMPLE_ID --project PROJECT.json [--sample-dir DIR] [options]
#
# Production remote workers use input_json + project on shared storage; this script
# is not used by the worker poll loop.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -x "$PROJECT_ROOT/.venv/bin/methyl-extract" ]]; then
  RUNNER=("$PROJECT_ROOT/.venv/bin/methyl-extract")
elif [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
  RUNNER=("$PROJECT_ROOT/.venv/bin/python" "-m" "methyl_worker.extract_runner")
else
  echo "error: activate repo .venv or install workers package" >&2
  exit 1
fi

SAMPLE_ID="${1:-}"
if [[ -z "$SAMPLE_ID" || "$SAMPLE_ID" == --* ]]; then
  echo "usage: $0 SAMPLE_ID --project PROJECT.json [--sample-dir DIR] [--reference-fasta PATH] [--chrom-mapping PATH]" >&2
  exit 1
fi
shift

exec "${RUNNER[@]}" "$SAMPLE_ID" "$@"
