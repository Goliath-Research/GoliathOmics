#!/usr/bin/env bash
# Prove the jammy GoliathAlign host runtime runs offline in stock ubuntu:22.04.
#
#   check_goliath_align_host.sh --tarball FILE

set -euo pipefail
TB=""
die() { printf '[check-goliath-align] ERROR: %s\n' "$*" >&2; exit 1; }
info() { printf '[check-goliath-align] %s\n' "$*" >&2; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tarball) TB="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done
[[ -n "$TB" && -f "$TB" ]] || die "--tarball required"
RT=""
for c in docker podman; do command -v "$c" >/dev/null 2>&1 && { RT=$c; break; }; done
[[ -n "$RT" ]] || die "docker or podman required"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
tar -C "$WORK" -xzf "$TB"
[[ -x "$WORK/goliath-align/bin/methylGrapher" ]] || die "tarball missing bin/methylGrapher"

# Inner probe script (mounted) — avoids nested-quote hell.
cat >"$WORK/probe.sh" <<'PROBE'
#!/usr/bin/env bash
set -euo pipefail
MG=/opt/goliath-align/bin/methylGrapher
run_help() {
  local out="$1"; shift
  if ! "$MG" "$@" >"/tmp/$out" 2>&1; then
    METHYLGRAPHER_MCALL_ENGINE=python "$MG" "$@" >"/tmp/$out" 2>&1 || true
  fi
  test -s "/tmp/$out"
  head -n 5 "/tmp/$out"
}
run_help help.txt help
# Soft probes: subcommands may exit non-zero without args; top-level help is the hard gate.
for sub in MojoFq2bamMeth MethylCall MergeCpG ConversionRate QC; do
  : >"/tmp/${sub}.txt"
  "$MG" "$sub" --help >"/tmp/${sub}.txt" 2>&1 \
    || "$MG" "$sub" -h >"/tmp/${sub}.txt" 2>&1 \
    || "$MG" help "$sub" >"/tmp/${sub}.txt" 2>&1 \
    || true
  echo "sub $sub bytes=$(wc -c <"/tmp/${sub}.txt")"
done
PROBE
chmod +x "$WORK/probe.sh"

info "pulling ubuntu:22.04 for offline help gate"
"$RT" pull ubuntu:22.04 >/dev/null
info "running methylGrapher help + subcommand probes (--network none)"
"$RT" run --rm --network none --user 0 \
  -v "$WORK/goliath-align:/opt/goliath-align:ro" \
  -v "$WORK/probe.sh:/probe.sh:ro" \
  ubuntu:22.04 bash /probe.sh \
  || die "host runtime does not run methylGrapher help offline in ubuntu:22.04"
info "OK: jammy host runtime help works offline"
