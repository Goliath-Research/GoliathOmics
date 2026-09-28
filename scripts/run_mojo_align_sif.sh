#!/usr/bin/env bash
echo "warning: scripts/run_mojo_align_sif.sh is deprecated; use scripts/run_goliath_align_sif.sh" >&2
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run_goliath_align_sif.sh" "$@"
