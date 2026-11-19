"""19_*: fold the legacy Omics* classes into omics/* and type the reference fields as relations."""

from __future__ import annotations

import subprocess
import sys

import pytest

# Sibling import (see test_omics_lab_inv_semantic.py): reuse the legacy-reconcile database.
from test_omics_meta_class_reconcile_legacy import (  # noqa: F401
    REPO_ROOT,
    SCHEMA_DIR,
    _psql,
    pg_legacy_reconcile_env,
)

LEGACY = ("OmicsDisease", "OmicsCustomer", "OmicsPatient", "OmicsSample", "OmicsLabBatch")
LEGACY_SQL = ", ".join(f"'{n}'" for n in LEGACY)


def _apply(env: dict[str, str], name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-f", str(SCHEMA_DIR / name)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="module")
def folded_env(pg_legacy_reconcile_env: dict[str, str]) -> dict[str, str]:  # noqa: F811
    env = pg_legacy_reconcile_env
    assert _psql(f"""SELECT count(*) FROM "Meta"."Classes" WHERE "Name" IN ({LEGACY_SQL})""", env) == "5"
    # Production legacy classes repeat their parent's fields (17_* keeps them as subclasses).
    _psql(
        f"""INSERT INTO "Meta"."Fields" ("ClassID", "Name", "TypeID", "Required")
            SELECT l."ID", f."Name", f."TypeID", f."Required"
            FROM "Meta"."Classes" l
            JOIN "Meta"."Fields" f ON f."ClassID" = l."ParentID"
            WHERE l."Name" IN ({LEGACY_SQL})
            ON CONFLICT DO NOTHING""",
        env,
    )
    assert _psql(
        f"""SELECT count(*) FROM "Meta"."Fields" f JOIN "Meta"."Classes" c ON c."ID" = f."ClassID"
            WHERE c."Name" IN ({LEGACY_SQL})""",
        env,
    ) == "25"
    for name in ("18_omics_study_meta_link.sql", "19_omics_meta_legacy_class_fold.sql"):
        proc = _apply(env, name)
        assert proc.returncode == 0, proc.stderr
    subprocess.run(
        [sys.executable, "-m", "goliath_omics.seed.semantic_catalog"],
        env={**env, "PYTHONPATH": str(REPO_ROOT / "src")},
        check=True,
        cwd=str(REPO_ROOT),
    )
    return env


def test_fold_removes_only_the_legacy_classes_views_and_types(folded_env: dict[str, str]) -> None:
    env = folded_env
    assert _psql(f"""SELECT count(*) FROM "Meta"."Classes" WHERE "Name" IN ({LEGACY_SQL})""", env) == "0"
    for name in LEGACY:
        assert _psql(f"""SELECT to_regclass('"Meta"."V{name}Legacy"') IS NULL""", env) == "t"
        assert _psql(f"""SELECT count(*) FROM wf.data_type WHERE name = 'meta/class/{name}'""", env) == "0"
    for view in ("VOmicsDisease", "VOmicsCustomer", "VOmicsPatient", "VOmicsSample", "VOmicsLabBatch"):
        assert _psql(f"""SELECT to_regclass('"Meta"."{view}"') IS NOT NULL""", env) == "t", view
    counts = _psql(
        """SELECT string_agg(c."Name" || '=' || n, ',' ORDER BY c."Name")
           FROM (SELECT "ClassID", count(*) n FROM "Meta"."Fields" GROUP BY 1) f
           JOIN "Meta"."Classes" c ON c."ID" = f."ClassID"
           WHERE c."Name" IN ('omics/Customer','omics/Disease','omics/LabBatch','omics/Patient','omics/Sample')""",
        env,
    )
    assert counts == "omics/Customer=3,omics/Disease=3,omics/LabBatch=3,omics/Patient=8,omics/Sample=8"


def test_reference_fields_are_relations_and_views_keep_integer_columns(folded_env: dict[str, str]) -> None:
    env = folded_env
    rows = _psql(
        """SELECT c."Name" || '.' || f."Name" || '=' || t.name || ':' || (t.schema_json->'x-goliath-relation'->>'key')
           FROM "Meta"."Fields" f JOIN "Meta"."Classes" c ON c."ID" = f."ClassID"
           JOIN wf.data_type t ON t.id = f."TypeID"
           WHERE t.schema_json ? 'x-goliath-relation' AND c."Name" LIKE 'omics/%'
           ORDER BY 1""",
        env,
    ).splitlines()
    assert rows == [
        "omics/Customer.organization_id=omics/ref/organization:organization_id",
        "omics/Disease.parent_id=omics/ref/disease:disease_id",
        "omics/LabBatch.lab_id=omics/ref/lab:lab_id",
        "omics/Patient.institution_id=omics/ref/institution:ID",
        "omics/Sample.customer_id=omics/ref/customer:customer_id",
        "omics/Sample.disease_id=omics/ref/disease:disease_id",
        "omics/Sample.patient_id=omics/ref/patient:patient_id",
    ]
    # The relation type keeps the field's kind, so the view column type does not change.
    col_types = _psql(
        """SELECT string_agg(DISTINCT format_type(atttypid, atttypmod), ',') FROM pg_attribute
           WHERE attrelid = '"Meta"."VOmicsSample"'::regclass
             AND attname IN ('patient_id', 'legacy_sample_id')""",
        env,
    )
    assert "," not in col_types, col_types


def test_attributes_read_fields_of_their_owning_class(folded_env: dict[str, str]) -> None:
    env = folded_env
    assert _psql("SELECT count(*) FROM omics_clinical.fn_semantic_attribute_fields() WHERE status = 'missing'", env) == "0"
    per_entity = _psql(
        """SELECT string_agg(entity || '=' || n, ',' ORDER BY entity) FROM (
             SELECT entity, count(*) n FROM omics_clinical.fn_semantic_attribute_fields()
             WHERE status = 'field' GROUP BY 1) s
           WHERE entity IN ('omics.customer','omics.disease','omics.lab_batch','omics.patient','omics.sample')""",
        env,
    )
    assert per_entity == "omics.customer=3,omics.disease=3,omics.lab_batch=3,omics.patient=8,omics.sample=8"
    proc = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "verify_omics_semantic_fields.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "legacy Omics* classes remaining: 0" in proc.stdout


def test_fold_is_idempotent_and_aborts_on_a_referenced_legacy_class(folded_env: dict[str, str]) -> None:
    env = folded_env
    proc = _apply(env, "19_omics_meta_legacy_class_fold.sql")
    assert proc.returncode == 0, proc.stderr
    _psql(
        """INSERT INTO "Meta"."Classes" ("Name", "ParentID", "TableName")
           SELECT 'OmicsDisease', c."ID", 'OmicsDiseaseLegacy' FROM "Meta"."Classes" c WHERE c."Name" = 'omics/Disease'""",
        env,
    )
    _psql(
        """INSERT INTO "Meta"."Classes" ("Name", "ParentID")
           SELECT 'test/OmicsDiseaseChild', c."ID" FROM "Meta"."Classes" c WHERE c."Name" = 'OmicsDisease'""",
        env,
    )
    try:
        proc = _apply(env, "19_omics_meta_legacy_class_fold.sql")
        assert proc.returncode != 0
        assert "subclasses=1" in proc.stderr
        assert _psql("""SELECT count(*) FROM "Meta"."Classes" WHERE "Name" = 'OmicsDisease'""", env) == "1"
    finally:
        _psql(
            """UPDATE "Meta"."Classes" SET "TypeID" = NULL WHERE "Name" = 'test/OmicsDiseaseChild';
               DELETE FROM wf.data_type WHERE class_id = (
                 SELECT "ID" FROM "Meta"."Classes" WHERE "Name" = 'test/OmicsDiseaseChild');
               DELETE FROM "Meta"."Classes" WHERE "Name" = 'test/OmicsDiseaseChild';
               DROP VIEW IF EXISTS "Meta"."Vtest/OmicsDiseaseChild";""",
            env,
        )
    proc = _apply(env, "19_omics_meta_legacy_class_fold.sql")
    assert proc.returncode == 0, proc.stderr
    assert _psql("""SELECT count(*) FROM "Meta"."Classes" WHERE "Name" = 'OmicsDisease'""", env) == "0"
