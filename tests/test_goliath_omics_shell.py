"""R2 child-app shell: domain schema order and goliath_omics stubs."""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ORDER_SH = REPO_ROOT / "scripts" / "domain_schema_order.sh"
SCHEMA_DIR = REPO_ROOT / "database/schema"
SQL_PG = REPO_ROOT / "workflow_engine/sql_pg"
APPLY_SH = REPO_ROOT / "workflow_engine/sql_pg/apply_schema.sh"


def _parse_bash_array(var_name: str, text: str) -> list[str]:
    pattern = rf"{var_name}=\(\s*(.*?)\s*\)"
    match = re.search(pattern, text, re.DOTALL)
    assert match is not None, f"{var_name} not found"
    block = match.group(1)
    return re.findall(r"(\S+\.sql)", block)


def test_domain_schema_order_lists_existing_files() -> None:
    text = ORDER_SH.read_text(encoding="utf-8")
    canonical = _parse_bash_array("DOMAIN_SCHEMA_SCRIPTS", text)
    legacy = _parse_bash_array("DOMAIN_SCHEMA_LEGACY_NAMES", text)
    assert len(canonical) == len(legacy) == 13
    for name in canonical:
        path = SCHEMA_DIR / name
        assert path.is_file(), name
        assert not path.is_symlink()
    for name in legacy:
        path = SQL_PG / name
        assert path.is_symlink(), name


def test_legacy_scripts_match_domain_order() -> None:
    order_text = ORDER_SH.read_text(encoding="utf-8")
    apply_text = APPLY_SH.read_text(encoding="utf-8")
    canonical = _parse_bash_array("DOMAIN_SCHEMA_LEGACY_NAMES", order_text)
    legacy = _parse_bash_array("SCRIPTS", apply_text)
    assert legacy == canonical


def test_legacy_apply_schema_delegates_to_domain_apply() -> None:
    apply = APPLY_SH.read_text(encoding="utf-8")
    assert "apply_domain_schema.sh" in apply


def test_semantic_export_json() -> None:
    from goliath_omics.semantic.catalog import SEMANTIC_CATALOG, SEMANTIC_REQUESTS
    from goliath_omics.semantic.export import export_catalog_document

    payload = export_catalog_document()
    assert len(payload["entities"]) == len(SEMANTIC_CATALOG)
    assert len(payload["requests"]) == len(SEMANTIC_REQUESTS)
    assert all(k.startswith("omics.") for k in payload["entities"])


def test_semantic_catalog_namespace() -> None:
    from goliath_omics.semantic.catalog import SEMANTIC_CATALOG

    assert SEMANTIC_CATALOG
    assert all(e.namespace == "omics" for e in SEMANTIC_CATALOG)


def test_bundle_omics_sql_list() -> None:
    import subprocess
    import sys

    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/bundle_omics_sql.py"),
            "--sql-dir",
            str(SQL_PG),
            "--list",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    names = proc.stdout.strip().splitlines()
    assert names == _parse_bash_array("DOMAIN_SCHEMA_LEGACY_NAMES", ORDER_SH.read_text(encoding="utf-8"))
