#!/usr/bin/env bash
# Deprecated: GoliathAlign is installed automatically from the release tarball
# on every worker boot (scripts/goliath_align_local.sh). Kept as a one-line
# redirect for older docs.
echo "warning: stage_goliath_align_host.sh is obsolete; release-assemble ships" >&2
echo "warning: goliath-align-host-linux-amd64-jammy.tar.gz and start_shared_worker" >&2
echo "warning: installs it via goliath_align_local.sh" >&2
exit 1
