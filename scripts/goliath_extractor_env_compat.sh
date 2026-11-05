#!/bin/bash
# Shared GoliathExtractor env fallbacks (one release): prefer GOLIATH_EXTRACTOR_*,
# read deprecated METHYL_EXTRACTOR_* when unset, emit a single stderr warning.

_goliath_extractor_migration_doc="docs/operators/goliath-extractor-migration.md"

_goliath_extractor_warn_deprecated_env() {
  local legacy="$1" modern="$2"
  echo "[DEPRECATED] ${legacy} is deprecated; set ${modern} instead (see ${_goliath_extractor_migration_doc})" >&2
}

# Print the value for NEW_KEY, else OLD_KEY with a deprecation warning.
_goliath_extractor_env_or_legacy() {
  local new_key="$1" old_key="$2"
  local new_val="${!new_key:-}" old_val="${!old_key:-}"
  if [[ -n "$new_val" ]]; then
    printf '%s\n' "$new_val"
    return 0
  fi
  if [[ -n "$old_val" ]]; then
    _goliath_extractor_warn_deprecated_env "$old_key" "$new_key"
    printf '%s\n' "$old_val"
    return 0
  fi
  return 1
}

_goliath_extractor_bundle_suite() {
  if [[ -n "${GOLIATH_EXTRACTOR_BUNDLE_SUITE:-}" ]]; then
    printf '%s\n' "$GOLIATH_EXTRACTOR_BUNDLE_SUITE"
  elif [[ -n "${METHYL_EXTRACTOR_BUNDLE_SUITE:-}" ]]; then
    _goliath_extractor_warn_deprecated_env "METHYL_EXTRACTOR_BUNDLE_SUITE" "GOLIATH_EXTRACTOR_BUNDLE_SUITE"
    printf '%s\n' "$METHYL_EXTRACTOR_BUNDLE_SUITE"
  else
    printf '%s\n' "jammy"
  fi
}

_goliath_extractor_bundle_debs() {
  if [[ -n "${GOLIATH_EXTRACTOR_BUNDLE_DEBS:-}" ]]; then
    printf '%s\n' "$GOLIATH_EXTRACTOR_BUNDLE_DEBS"
  elif [[ -n "${METHYL_EXTRACTOR_BUNDLE_DEBS:-}" ]]; then
    _goliath_extractor_warn_deprecated_env "METHYL_EXTRACTOR_BUNDLE_DEBS" "GOLIATH_EXTRACTOR_BUNDLE_DEBS"
    printf '%s\n' "$METHYL_EXTRACTOR_BUNDLE_DEBS"
  else
    printf '%s\n' "libhts3 libdeflate0 libhtscodecs2"
  fi
}

_goliath_extractor_bundle_libs() {
  if [[ -n "${GOLIATH_EXTRACTOR_BUNDLE_LIBS:-}" ]]; then
    printf '%s\n' "$GOLIATH_EXTRACTOR_BUNDLE_LIBS"
  elif [[ -n "${METHYL_EXTRACTOR_BUNDLE_LIBS:-}" ]]; then
    _goliath_extractor_warn_deprecated_env "METHYL_EXTRACTOR_BUNDLE_LIBS" "GOLIATH_EXTRACTOR_BUNDLE_LIBS"
    printf '%s\n' "$METHYL_EXTRACTOR_BUNDLE_LIBS"
  else
    printf '%s\n' "libhts.so.3 libdeflate.so.0 libhtscodecs.so.2"
  fi
}

_goliath_extractor_require_patchelf() {
  if [[ -n "${GOLIATH_EXTRACTOR_REQUIRE_PATCHELF:-}" ]]; then
    printf '%s\n' "$GOLIATH_EXTRACTOR_REQUIRE_PATCHELF"
  elif [[ -n "${METHYL_EXTRACTOR_REQUIRE_PATCHELF:-}" ]]; then
    _goliath_extractor_warn_deprecated_env "METHYL_EXTRACTOR_REQUIRE_PATCHELF" "GOLIATH_EXTRACTOR_REQUIRE_PATCHELF"
    printf '%s\n' "$METHYL_EXTRACTOR_REQUIRE_PATCHELF"
  else
    printf '%s\n' "0"
  fi
}
