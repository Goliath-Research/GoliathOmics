#!/usr/bin/env bash
echo "warning: scripts/build_mojo_align_image.sh is deprecated; use scripts/build_goliath_align_image.sh" >&2
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/build_goliath_align_image.sh" "$@"
