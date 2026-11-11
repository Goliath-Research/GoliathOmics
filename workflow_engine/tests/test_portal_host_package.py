"""Portal host: GoliathApp baseline + apply_domain_schema on fresh Postgres."""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BASELINE_REF_FILE = REPO / ".goliath" / "goliathapp_portal_baseline.ref"
DOMAIN_APPLY = REPO / "scripts" / "apply_domain_schema.sh"
TEST_DB = "goliathomics_portal_host_test"


def _app_sql_dir() -> Path:
    override = os.environ.get("GOLIATH_APP_SQL_DIR")
    if override:
        return Path(override)
    checkout = REPO / "_goliathapp_portal" / "workflow_engine" / "sql_pg"
    if checkout.is_dir():
        return checkout
    pin = BASELINE_REF_FILE.read_text(encoding="utf-8").strip() if BASELINE_REF_FILE.is_file() else "(unknown)"
    raise FileNotFoundError(
        f"GoliathApp sql_pg not found; set GOLIATH_APP_SQL_DIR or check out ref {pin} to _goliathapp_portal"
    )


def _pg_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PGHOST", os.environ.get("POSTGRES_HOST", "localhost"))
    env.setdefault("PGPORT", os.environ.get("POSTGRES_PORT", "5432"))
    env.setdefault("PGUSER", os.environ.get("POSTGRES_USER", "postgres"))
    env.setdefault("PGPASSWORD", os.environ.get("POSTGRES_PASSWORD", "methyl"))
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


def _psql_file(path: Path, env: dict[str, str]) -> None:
    subprocess.run(["psql", "-q", "-v", "ON_ERROR_STOP=1", "-f", str(path)], env=env, check=True)


@pytest.fixture(scope="module")
def portal_host_env() -> dict[str, str]:
    from shutil import which

    if not which("psql"):
        pytest.skip("psql not installed")
    app_sql = _app_sql_dir()
    apply_sh = app_sql / "apply_schema.sh"
    nav_seed = app_sql / "seed_platform_nav_tree.sql"
    if not apply_sh.is_file():
        pytest.skip(f"GoliathApp apply_schema.sh missing at {apply_sh}")
    if not nav_seed.is_file():
        pytest.skip(f"seed_platform_nav_tree.sql missing at {nav_seed}")
    if not DOMAIN_APPLY.is_file():
        pytest.skip(f"apply_domain_schema.sh missing at {DOMAIN_APPLY}")

    admin = _pg_env()
    admin["PGDATABASE"] = "postgres"
    try:
        _psql("SELECT 1", admin)
    except RuntimeError:
        pytest.skip("PostgreSQL not reachable")
    _psql(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)', admin)
    _psql(f'CREATE DATABASE "{TEST_DB}"', admin)
    env = _pg_env()
    env["PGDATABASE"] = TEST_DB
    subprocess.run(["bash", str(apply_sh)], env=env, check=True, cwd=str(app_sql))
    email = f"portal-pkg-{uuid.uuid4().hex[:8]}@test"
    user_id = int(
        _psql(
            f"""
            INSERT INTO "RBAC"."Users" ("FullName", email, "Active")
            VALUES ('Portal Pkg', '{email}', 1) RETURNING "ID"
            """,
            env,
        )
    )
    org_row = _psql(f"SELECT * FROM portal.sp_organization_create('PortalPkg-{user_id}')", env)
    org_id = int(org_row.split("|")[0])
    _psql(f"SELECT grant_id FROM portal.sp_organization_set_admin({org_id}, {user_id})", env)
    _psql_file(nav_seed, env)
    subprocess.run(["bash", str(DOMAIN_APPLY)], env=env, check=True, cwd=str(REPO))
    return env


def test_omics_sample_subclasses_platform_material(portal_host_env: dict[str, str]) -> None:
    parent = _psql(
        """
        SELECT parent."Name"
        FROM "Meta"."Classes" child
        INNER JOIN "Meta"."Classes" parent ON parent."ID" = child."ParentID"
        WHERE child."Name" = 'omics/Sample'
        """,
        portal_host_env,
    )
    assert parent == "platform/Material"


def test_omics_nav_registered(portal_host_env: dict[str, str]) -> None:
    count = int(
        _psql(
            """SELECT COUNT(*) FROM portal."NavTree" WHERE "NodePath" LIKE '/app/omics%'""",
            portal_host_env,
        )
    )
    assert count >= 5


def test_omics_customers_nav_uses_meta_object_frame(portal_host_env: dict[str, str]) -> None:
    row = _psql(
        """
        SELECT ni."FrameKey", ni."Name"
        FROM portal."NavTree" n
        INNER JOIN portal."NodeInfos" ni ON ni."Id" = n."NodeInfoId"
        WHERE n."NodePath" = '/app/omics/customers'
        LIMIT 1
        """,
        portal_host_env,
    )
    assert row == "meta_object|platform.nav.meta_object:omics/Customer"


def test_god_architect_nav_and_permissions_superset(portal_host_env: dict[str, str]) -> None:
    god_id = int(_psql("SELECT portal.fn_god_architect_role_id()", portal_host_env))
    admin_id = int(
        _psql(
            """SELECT "ID" FROM "RBAC"."Roles" WHERE "Name" = 'organization_admin' LIMIT 1""",
            portal_host_env,
        )
    )
    admin_perms = _psql(
        f"""
        SELECT string_agg(p."Name", ',' ORDER BY p."Name")
        FROM "RBAC"."Role_Permissions" rp
        INNER JOIN "RBAC"."Permissions" p ON p."ID" = rp."PermissionID"
        WHERE rp."RoleID" = {admin_id}
        """,
        portal_host_env,
    )
    god_perms = _psql(
        f"""
        SELECT string_agg(p."Name", ',' ORDER BY p."Name")
        FROM "RBAC"."Role_Permissions" rp
        INNER JOIN "RBAC"."Permissions" p ON p."ID" = rp."PermissionID"
        WHERE rp."RoleID" = {god_id}
        """,
        portal_host_env,
    )
    assert admin_perms
    assert god_perms
    assert "platform.model.architect" in god_perms.split(",")
    if (
        _psql(
            "SELECT to_regprocedure('portal.sp_sync_god_architect_operator_entitlements()') IS NOT NULL",
            portal_host_env,
        )
        == "t"
    ):
        assert set(admin_perms.split(",")) <= set(god_perms.split(","))

    omics_god_nav = int(
        _psql(
            f"""
            SELECT COUNT(*)
            FROM portal."Role2Node" r2n
            INNER JOIN portal."NavTree" n ON n."ID" = r2n."NodeId"
            WHERE r2n."RoleId" = {god_id}
              AND n."NodePath" LIKE '/app/omics%'
            """,
            portal_host_env,
        )
    )
    omics_admin_nav = int(
        _psql(
            f"""
            SELECT COUNT(*)
            FROM portal."Role2Node" r2n
            INNER JOIN portal."NavTree" n ON n."ID" = r2n."NodeId"
            WHERE r2n."RoleId" = {admin_id}
              AND n."NodePath" LIKE '/app/omics%'
            """,
            portal_host_env,
        )
    )
    assert omics_god_nav >= omics_admin_nav >= 5
