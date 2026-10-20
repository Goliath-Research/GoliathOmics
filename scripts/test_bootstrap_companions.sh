#!/bin/bash
# Every sibling file that an installed companion unconditionally sources or
# reads from its own directory must also be installed into $ROOT/bin by
# goliath-worker-bootstrap.sh (install_companion). Otherwise boot fails with
# "/work/goliath/bin/<file>: No such file or directory".
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOOT="$ROOT/deploy/runpod/goliath-worker-bootstrap.sh"
fail() { echo "FAIL: $*" >&2; exit 1; }

installed="$(sed -n 's/^[[:space:]]*install_companion "\([^"]*\)".*/\1/p' "$BOOT")"
[[ -n "$installed" ]] || fail "no install_companion calls in $BOOT"
names="$(printf '%s\n' "$installed" | xargs -n1 basename)"

# Walk companions transitively for '<DIR_VAR>/<file>' sources/reads, where
# DIR_VAR is the script's own-dir variable.
queue=$(printf '%s\n' "$installed")
seen=""
missing=0
while [[ -n "$queue" ]]; do
  rel="$(printf '%s\n' "$queue" | head -n1)"
  queue="$(printf '%s\n' "$queue" | tail -n +2)"
  [[ " $seen " == *" $rel "* ]] && continue
  seen="$seen $rel"
  f="$ROOT/$rel"
  [[ -f "$f" ]] || fail "$rel listed in install_companion but missing from repo"
  [[ "$f" == *.sh ]] || continue
  # Unconditional "source \"$X_DIR/name\"" lines, plus "$SCRIPT_DIR/name.env" reads.
  deps="$(grep -oE '^[[:space:]]*(source|\.)[[:space:]]+"\$(START_SCRIPT_DIR|SCRIPT_DIR|HERE)/[A-Za-z0-9_.-]+"' "$f" \
            | sed -E 's/.*\/([A-Za-z0-9_.-]+)"$/\1/' || true)"
  deps="$deps $(grep -oE '\$SCRIPT_DIR/[A-Za-z0-9_.-]+\.env' "$f" | sed 's/.*\///' || true)"
  for d in $deps; do
    if ! grep -qx "$d" <<<"$names"; then
      echo "FAIL: $rel needs sibling $d, but bootstrap does not install_companion it" >&2
      missing=1
      continue
    fi
    dep_rel="$(printf '%s\n' "$installed" | awk -v n="$d" -F/ '$NF==n' | head -n1)"
    queue="$(printf '%s\n%s\n' "$queue" "$dep_rel")"
  done
done
[[ "$missing" == 0 ]] || exit 1

# Each install_companion marker must appear in its file.
while read -r rel marker; do
  grep -qF "$marker" "$ROOT/$rel" || fail "marker '$marker' not found in $rel"
done < <(sed -n 's/^[[:space:]]*install_companion "\([^"]*\)" "\([^"]*\)".*/\1 \2/p' "$BOOT")

echo "ok: bootstrap installs every sourced sibling ($(echo $names | tr '\n' ' '))"
