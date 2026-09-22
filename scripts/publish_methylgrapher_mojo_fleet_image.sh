#!/usr/bin/env bash
# Deprecated wrapper — use scripts/publish_mojo_align_fleet_image.sh
echo "warning: publish_methylgrapher_mojo_fleet_image.sh is deprecated; use publish_mojo_align_fleet_image.sh" >&2
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/publish_mojo_align_fleet_image.sh" "$@"
