"""Shared names for the GoliathExtractor CLI (formerly MethylExtractor)."""

from __future__ import annotations

import logging
import os
import shutil

_logger = logging.getLogger(__name__)

_MIGRATION_DOC = "docs/operators/goliath-extractor-migration.md"

EXTRACTOR_CLI = "goliath-extractor"
LEGACY_EXTRACTOR_CLI = "MethylExtractor"
DEFAULT_EXTRACTOR_CLI = EXTRACTOR_CLI

EXTRACTOR_TARBALL_PREFIX = "goliath-extractor-linux"
LEGACY_EXTRACTOR_TARBALL_PREFIX = "methyl-extractor-linux"

INSTALL_DIR_PREFIX = "goliath-extractor"
LEGACY_INSTALL_DIR_PREFIX = "methyl-extractor"

EXTRACTION_MANIFEST_SCHEMA = "goliath_extractor.extraction_manifest"
LEGACY_EXTRACTION_MANIFEST_SCHEMA = "methylextractor.extraction_manifest"
EXTRACTION_MANIFEST_SCHEMAS = frozenset(
    {EXTRACTION_MANIFEST_SCHEMA, LEGACY_EXTRACTION_MANIFEST_SCHEMA}
)

EXTRACTOR_METADATA_NAME = "GoliathExtractor"
LEGACY_EXTRACTOR_METADATA_NAME = "MethylExtractor"

GITHUB_REPO = "Goliath-Research/GoliathExtractor"
LEGACY_GITHUB_REPO = "Goliath-Research/MethylExtractor"

DOCS_SITE = "https://goliath-research.github.io/GoliathExtractor/"


def _warn_legacy_env(legacy: str, modern: str) -> None:
    _logger.warning(
        "%s is deprecated; set %s instead (see %s)",
        legacy,
        modern,
        _MIGRATION_DOC,
    )


def is_extraction_manifest_schema(name: object) -> bool:
    return isinstance(name, str) and name in EXTRACTION_MANIFEST_SCHEMAS


def resolve_extractor_bin_env() -> str:
    value = os.environ.get("GOLIATH_EXTRACTOR_BIN", "").strip()
    if value:
        return value
    legacy = os.environ.get("METHYL_EXTRACTOR_BIN", "").strip()
    if legacy:
        _warn_legacy_env("METHYL_EXTRACTOR_BIN", "GOLIATH_EXTRACTOR_BIN")
        return legacy
    found = shutil.which(EXTRACTOR_CLI)
    if found:
        return found
    legacy_bin = shutil.which(LEGACY_EXTRACTOR_CLI)
    if legacy_bin:
        return legacy_bin
    return ""


def resolve_extractor_home_env() -> str:
    value = os.environ.get("GOLIATH_EXTRACTOR_HOME", "").strip()
    if value:
        return value.rstrip("/")
    legacy = os.environ.get("METHYL_EXTRACTOR_HOME", "").strip()
    if legacy:
        _warn_legacy_env("METHYL_EXTRACTOR_HOME", "GOLIATH_EXTRACTOR_HOME")
        return legacy.rstrip("/")
    return ""


def manifest_extractor_tarball(artifacts: dict[str, object]) -> str:
    """Return tarball filename from a manifest ``artifacts[arch]`` block."""
    if not isinstance(artifacts, dict):
        return ""
    value = artifacts.get("goliath_extractor") or artifacts.get("methyl_extractor")
    return str(value) if value else ""
