"""Apply GoliathApp then GoliathOmics PostgreSQL schema twice (idempotent)."""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from pathlib import Path
from shutil import which

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_SQL = Path(os.environ.get("GOLIATH_APP_SQL_DIR", REPO_ROOT / "_goliathapp" / "workflow_engine" / "sql_pg"))
OMICS_SQL = REPO_ROOT / "workflow_engine" / "sql_pg"
APP_APPLY = APP_SQL / "apply_schema.sh"
OMICS_APPLY = OMICS_SQL / "apply_schema.sh"
DOMAIN_APPLY = REPO_ROOT / "scripts" / "apply_domain_schema.sh"


def _pg_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PGHOST", os.environ.get("POSTGRES_HOST", "localhost"))
    env.setdefault("PGPORT", os.environ.get("POSTGRES_PORT", "5432"))
    env.setdefault("PGUSER", os.environ.get("POSTGRES_USER", "postgres"))
    env.setdefault("PGDATABASE", os.environ.get("POSTGRES_DB", "goliath_omics_schema_test"))
    if env.get("POSTGRES_PASSWORD"):
        env["PGPASSWORD"] = env["POSTGRES_PASSWORD"]
    env.setdefault("PGPASSWORD", os.environ.get("PGPASSWORD", "methyl"))
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


@pytest.fixture(scope="module")
def pg_schema_applied() -> dict[str, str]:
    if not which("psql"):
        pytest.skip("psql not installed")
    if not APP_APPLY.is_file():
        pytest.skip(f"GoliathApp apply_schema.sh not found at {APP_APPLY}")
    if not OMICS_APPLY.is_file():
        pytest.skip(f"Omics apply_schema.sh not found at {OMICS_APPLY}")
    env = _pg_env()
    try:
        _psql("SELECT 1", env)
    except RuntimeError:
        pytest.skip("PostgreSQL not reachable for schema apply test")
    for _ in range(2):
        subprocess.run(["bash", str(APP_APPLY)], env=env, check=True, cwd=str(APP_SQL))
        subprocess.run(["bash", str(OMICS_APPLY)], env=env, check=True, cwd=str(OMICS_SQL))
    return env


def test_apply_domain_schema_script_exists() -> None:
    assert DOMAIN_APPLY.is_file()
    text = DOMAIN_APPLY.read_text(encoding="utf-8")
    assert "domain_schema_order.sh" in text


def test_many_customers_per_organization(pg_schema_applied: dict[str, str]) -> None:
    env = pg_schema_applied
    org_row = _psql("SELECT * FROM portal.sp_organization_create('Omics P1b org')", env)
    org_id, _scope_id = org_row.split("|")
    suffix = uuid.uuid4().hex[:8]
    c1 = _psql(
        f"""
        INSERT INTO portal."Customers" ("Name", "OrganizationID")
        VALUES ('cust-a-{suffix}', {org_id})
        RETURNING "ID"
        """,
        env,
    )
    c2 = _psql(
        f"""
        INSERT INTO portal."Customers" ("Name", "OrganizationID")
        VALUES ('cust-b-{suffix}', {org_id})
        RETURNING "ID"
        """,
        env,
    )
    assert int(c1) > 0 and int(c2) > 0 and c1 != c2
    count = _psql(
        f'SELECT count(*) FROM portal."Customers" WHERE "OrganizationID" = {org_id}',
        env,
    )
    assert int(count) >= 2


def test_clinical_nav_and_disease_list(pg_schema_applied: dict[str, str]) -> None:
    env = pg_schema_applied
    diseases = _psql("SELECT count(*) FROM portal.sp_list_diseases()", env)
    assert diseases.isdigit()
    nav = _psql(
        "SELECT count(*) FROM portal.\"NavTree\" n "
        "JOIN portal.\"NodeInfos\" i ON i.\"Id\" = n.\"InfoID\" "
        "WHERE i.\"Name\" = 'omics_clinical'",
        env,
    )
    assert int(nav) >= 1


def test_clinical_record_history_records_changes(pg_schema_applied: dict[str, str]) -> None:
    """20_*: every clinical table has history; no-op updates record nothing; history is append-only."""
    env = pg_schema_applied
    untracked = _psql(
        """
        SELECT count(*) FROM unnest(ARRAY['portal."Patients"', 'portal."Samples"', 'portal."Institutions"',
                                          'portal."Customers"', 'omics_clinical.study_meta_link']) t(name)
        WHERE NOT EXISTS (SELECT 1 FROM pg_trigger g WHERE g.tgrelid = to_regclass(t.name)
                          AND g.tgname = 'trg_omics_record_history')
        """,
        env,
    )
    assert untracked == "0"
    name = f"history-test-{uuid.uuid4().hex[:8]}"
    inst_id = _psql(f"""INSERT INTO portal."Institutions" ("Name") VALUES ('{name}') RETURNING "ID" """, env)
    _psql(f"""UPDATE portal."Institutions" SET "Name" = "Name" WHERE "ID" = {inst_id}""", env)
    _psql(
        f"""SELECT set_config('goliath.actor_user_id', '7', false);
            UPDATE portal."Institutions" SET "Name" = '{name}-2' WHERE "ID" = {inst_id}""",
        env,
    )
    _psql(f"""DELETE FROM portal."Institutions" WHERE "ID" = {inst_id}""", env)
    rows = _psql(
        f"""
        SELECT string_agg(op || ':' || COALESCE(before_doc->>'Name', '-') || '>' || COALESCE(after_doc->>'Name', '-')
                          || ':' || COALESCE(actor_user_id::text, '-'), ',' ORDER BY history_id)
        FROM omics_clinical.record_history
        WHERE table_name = 'portal.Institutions' AND row_key = jsonb_build_object('ID', {inst_id})
        """,
        env,
    )
    assert rows == f"insert:->{name}:-,update:{name}>{name}-2:7,delete:{name}-2>-:-"
    for stmt in ("UPDATE omics_clinical.record_history SET op = 'insert'", "DELETE FROM omics_clinical.record_history"):
        with pytest.raises(RuntimeError, match="append-only"):
            _psql(stmt, env)
    if _psql("SELECT to_regprocedure('portal.fn_ledger_verify_all()') IS NOT NULL", env) == "t":
        assert _psql("SELECT count(*) FROM portal.fn_ledger_verify('omics_clinical.record_history')", env) == "0"


# --- Every timestamp is stored as UTC, whatever the session time zone (21_omics_utc_timestamps.sql) ---

NON_UTC_ZONES = ("America/New_York", "Asia/Kolkata", "Pacific/Kiritimati")
_UTC_WALL_CLOCK = re.compile(
    r"(now|clock_timestamp|statement_timestamp|transaction_timestamp)\(\)\s+AT\s+TIME\s+ZONE\s+'utc'", re.I
)
_TO_CHAR_UTC = re.compile(r"to_char\(\s*now\(\)\s+AT\s+TIME\s+ZONE\s+'utc'", re.I)
# A column declared `timestamp` / `timestamp without time zone` (a zoneless wall clock).
_ZONELESS_TIMESTAMP_COLUMN = re.compile(
    r"^\s*\"?\w+\"?\s+timestamp(\s+without\s+time\s+zone)?(\s*\(\d\))?\s*(NOT\s+NULL|NULL|DEFAULT|,|$)", re.I
)


def _zone_env(env: dict[str, str], zone: str) -> dict[str, str]:
    zoned = dict(env)
    zoned["PGTZ"] = zone
    return zoned


def test_sources_never_store_a_utc_wall_clock_in_timestamptz() -> None:
    sources = [p for p in (REPO_ROOT / "database" / "schema").glob("*.sql") if not p.name.startswith("21_")]
    sources += [p for p in OMICS_SQL.rglob("*.sql") if not p.is_symlink()]
    # scripts/test_*.sh build legacy fixtures on purpose; everything else must be clean.
    sources += [p for p in (REPO_ROOT / "scripts").glob("*.sh") if not p.name.startswith("test_")]
    sources += list((REPO_ROOT / "workflow_engine" / "ops").glob("*.py"))
    offenders = []
    for path in sorted(sources):
        text = _TO_CHAR_UTC.sub("", path.read_text(encoding="utf-8"))
        for lineno, line in enumerate(text.splitlines(), 1):
            if _UTC_WALL_CLOCK.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "use now() for timestamptz values:\n" + "\n".join(offenders)


def test_sources_declare_no_zoneless_timestamp_columns() -> None:
    sources = list((REPO_ROOT / "database" / "schema").glob("*.sql"))
    sources += [p for p in OMICS_SQL.rglob("*.sql") if not p.is_symlink()]
    sources += [p for p in (REPO_ROOT / "scripts").glob("*.sh") if not p.name.startswith("test_")]
    offenders = []
    for path in sorted(set(sources)):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _ZONELESS_TIMESTAMP_COLUMN.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "declare timestamp columns as timestamptz:\n" + "\n".join(offenders)


def test_no_zoneless_timestamp_columns_deployed(pg_schema_applied: dict[str, str]) -> None:
    rows = _psql(
        """SELECT string_agg(format('%I.%I.%I', n.nspname, c.relname, a.attname), ', ')
           FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped
             AND a.atttypid = 'timestamp'::regtype
             AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%'""",
        pg_schema_applied,
    )
    assert rows == "", rows


def test_no_utc_wall_clock_defaults_or_writes_deployed(pg_schema_applied: dict[str, str]) -> None:
    rows = _psql("SELECT object_kind || ' ' || object_name FROM omics_clinical.fn_utc_timestamp_violations()", pg_schema_applied)
    assert rows == ""


@pytest.mark.parametrize("zone", NON_UTC_ZONES)
def test_every_timestamp_default_is_the_current_instant_in_a_non_utc_session(
    pg_schema_applied: dict[str, str], zone: str
) -> None:
    env = _zone_env(pg_schema_applied, zone)
    late = _psql(
        """
        DO $$
        DECLARE r record; v_drift float8; v_bad text := ''; v_n int := 0;
        BEGIN
          FOR r IN
            SELECT format('%I.%I.%I', n.nspname, c.relname, a.attname) AS col, pg_get_expr(d.adbin, d.adrelid) AS expr
            FROM pg_attrdef d
            JOIN pg_class c ON c.oid = d.adrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
            WHERE n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema'
              AND c.relkind IN ('r', 'p') AND a.atttypid = 'timestamptz'::regtype
              AND pg_get_expr(d.adbin, d.adrelid) ~* '(now|clock_timestamp|statement_timestamp|transaction_timestamp|current_timestamp)'
          LOOP
            EXECUTE format('SELECT abs(extract(epoch FROM (%s) - now()))::float8', r.expr) INTO v_drift;
            v_n := v_n + 1;
            IF v_drift > 120 THEN v_bad := v_bad || r.col || ' off by ' || round(v_drift::numeric) || 's; '; END IF;
          END LOOP;
          IF v_n < 50 THEN RAISE EXCEPTION 'only % timestamp defaults found', v_n; END IF;
          IF v_bad <> '' THEN RAISE EXCEPTION 'non-UTC defaults: %', v_bad; END IF;
        END $$;
        SELECT 'ok';
        """,
        env,
    )
    assert late == "ok"


@pytest.mark.parametrize("zone", NON_UTC_ZONES)
def test_clinical_record_history_stamps_utc_instants_in_a_non_utc_session(
    pg_schema_applied: dict[str, str], zone: str
) -> None:
    env = _zone_env(pg_schema_applied, zone)
    name = f"utc-history-{uuid.uuid4().hex[:8]}"
    inst_id = _psql(f"""INSERT INTO portal."Institutions" ("Name") VALUES ('{name}') RETURNING "ID" """, env)
    drift = _psql(
        f"""
        SELECT max(abs(extract(epoch FROM recorded_at_utc - now())))::int
        FROM omics_clinical.record_history
        WHERE table_name = 'portal.Institutions' AND row_key = jsonb_build_object('ID', {inst_id})
        """,
        env,
    )
    assert drift != "" and int(drift) <= 120, f"record_history.recorded_at_utc off by {drift}s in {zone}"
    _psql(f"""DELETE FROM portal."Institutions" WHERE "ID" = {inst_id}""", env)


def test_utc_migration_rewrites_legacy_wall_clock_defaults(pg_schema_applied: dict[str, str]) -> None:
    env = pg_schema_applied
    table = f"utc_legacy_{uuid.uuid4().hex[:8]}"
    _psql(
        f"CREATE TABLE omics_clinical.{table} (id int, created_at timestamptz NOT NULL DEFAULT (now() AT TIME ZONE 'utc'))",
        env,
    )
    try:
        subprocess.run(
            ["psql", "-q", "-v", "ON_ERROR_STOP=1", "-f", str(REPO_ROOT / "database" / "schema" / "21_omics_utc_timestamps.sql")],
            env=env,
            check=True,
            capture_output=True,
        )
        expr = _psql(
            f"""SELECT pg_get_expr(d.adbin, d.adrelid) FROM pg_attrdef d
                JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
                WHERE d.adrelid = 'omics_clinical.{table}'::regclass AND a.attname = 'created_at'""",
            env,
        )
        assert expr == "now()"
        drift = _psql(
            f"""INSERT INTO omics_clinical.{table} (id) VALUES (1);
                SELECT abs(extract(epoch FROM created_at - now()))::int FROM omics_clinical.{table}""",
            _zone_env(env, "America/Los_Angeles"),
        )
        assert int(drift) <= 120
    finally:
        _psql(f"DROP TABLE IF EXISTS omics_clinical.{table}", env)
