"""Omics semantic catalog and export tests."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from goliath_omics.semantic.catalog import SEMANTIC_CATALOG, SEMANTIC_REQUESTS
from goliath_omics.semantic.export import export_catalog_document

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMITTED_CATALOG = REPO_ROOT / "schemas/semantic/catalog.json"


ALLOWED_ATTRIBUTE_ROLES = frozenset({"list", "detail", "filter", "key"})


def test_semantic_attribute_roles_are_portal_allowed() -> None:
    for entity in SEMANTIC_CATALOG:
        for attr in entity.attributes:
            assert attr.role in ALLOWED_ATTRIBUTE_ROLES, (
                f"{entity.code}.{attr.code} uses disallowed role {attr.role!r}"
            )


def test_core_clinical_entities_bind_omics_meta_classes() -> None:
    for code, class_name in (
        ("disease", "omics/Disease"),
        ("customer", "omics/Customer"),
        ("patient", "omics/Patient"),
        ("sample", "omics/Sample"),
        ("lab_batch", "omics/LabBatch"),
    ):
        entity = next(e for e in SEMANTIC_CATALOG if e.code == code)
        assert entity.meta_class_name == class_name


def test_entity_codes_unique() -> None:
    codes = [e.code for e in SEMANTIC_CATALOG]
    assert len(codes) == len(set(codes))


def test_requests_reference_known_entities() -> None:
    entity_codes = {e.code for e in SEMANTIC_CATALOG}
    for req in SEMANTIC_REQUESTS:
        assert req.entity_code in entity_codes
        assert req.namespace == "omics"
        assert req.request_code.startswith("omics.")


def test_export_document_matches_committed_catalog() -> None:
    live = export_catalog_document()
    committed_keys = set(live.keys()) - {"row_types_sql"}
    assert committed_keys == {"entities", "requests"}
    assert len(live["entities"]) == len(SEMANTIC_CATALOG)
    assert len(live["requests"]) == len(SEMANTIC_REQUESTS)
    assert COMMITTED_CATALOG.is_file()
    committed = json.loads(COMMITTED_CATALOG.read_text(encoding="utf-8"))
    assert set(committed.keys()) == {"entities", "requests"}


@pytest.fixture(scope="module")
def pg_semantic_env():
    from shutil import which

    if not which("psql"):
        pytest.skip("psql not installed")
    app_sql = Path(
        os.environ.get(
            "GOLIATH_APP_SQL_DIR",
            REPO_ROOT / "_goliathapp" / "workflow_engine" / "sql_pg",
        )
    )
    app_apply = app_sql / "apply_schema.sh"
    omics_apply = REPO_ROOT / "workflow_engine/sql_pg/apply_schema.sh"
    if not app_apply.is_file() or not omics_apply.is_file():
        pytest.skip("schema apply scripts missing")
    env = os.environ.copy()
    env.setdefault("PGHOST", os.environ.get("POSTGRES_HOST", "localhost"))
    env.setdefault("PGPORT", os.environ.get("POSTGRES_PORT", "5432"))
    env.setdefault("PGUSER", os.environ.get("POSTGRES_USER", "postgres"))
    env.setdefault("PGDATABASE", os.environ.get("POSTGRES_DB", "goliath_omics_semantic_test"))
    if env.get("POSTGRES_PASSWORD"):
        env["PGPASSWORD"] = env["POSTGRES_PASSWORD"]
    env.setdefault("PGPASSWORD", os.environ.get("PGPASSWORD", "methyl"))
    proc = subprocess.run(
        ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-t", "-A", "-c", "SELECT 1"],
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.skip("PostgreSQL not reachable")
    subprocess.run(["bash", str(app_apply)], env=env, check=True, cwd=str(app_sql))
    subprocess.run(["bash", str(omics_apply)], env=env, check=True, cwd=str(omics_apply.parent))
    return env


def _psql(sql: str, env: dict[str, str]) -> str:
    proc = subprocess.run(
        ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-t", "-A", "-c", sql],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return proc.stdout.strip()


def test_semantic_disease_list_null_user_returns_empty(pg_semantic_env: dict[str, str]) -> None:
    env = pg_semantic_env
    count = _psql(
        "SELECT count(*) FROM omics_clinical.fn_semantic_disease_list(NULL, '{}'::jsonb, 500)",
        env,
    )
    assert int(count) == 0


def test_semantic_disease_list_returns_catalog_rows(pg_semantic_env: dict[str, str]) -> None:
    env = pg_semantic_env
    suffix = uuid.uuid4().hex[:8]
    _psql(
        f"""
        INSERT INTO portal."Diseases" ("Name")
        VALUES ('semantic-disease-{suffix}')
        """,
        env,
    )
    count = _psql(
        "SELECT count(*) FROM omics_clinical.fn_semantic_disease_list(1, '{}'::jsonb, 500)",
        env,
    )
    assert int(count) >= 1
