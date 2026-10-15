#!/usr/bin/env bash
# RunPod image keepalive. This file is part of the RunPod adapter.
# The shared worker start does not know about /start.sh.
#
# The pytorch image's /start.sh keeps sshd (port 22) and Jupyter (port 8888)
# in the foreground. If that entrypoint is missing, sleep so the platform
# does not treat the container exit as a crash and restart-loop the pod.

set +x
set -euo pipefail

if [[ -x /start.sh ]]; then
  exec /start.sh "$@"
fi
printf '[goliath-runpod] /start.sh is not executable; sleeping so the pod is not restarted\n' >&2
exec sleep infinity
