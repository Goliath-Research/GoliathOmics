"""Omics sql_pg must not define GoliathApp shell tables or FK into platform shell."""

from __future__ import annotations

import re
from pathlib import Path

SQL_PG = Path(__file__).resolve().parents[1] / "sql_pg"

_SHELL_TABLE_DDL = re.compile(
    r'CREATE TABLE IF NOT EXISTS "portal"\."(NavTree|NodeInfos|Role2Node|UserRoles|Objs)"'
)

_FORBIDDEN_FK = re.compile(
    r'REFERENCES\s+("portal"\.)?"(NavTree|NodeInfos|Role2Node|UserRoles|Objs)"',
    re.IGNORECASE,
)

_ALLOWED_CROSS_SCHEMA = (
    'REFERENCES portal."Organizations"',
    'REFERENCES portal.resource_profile',
    'REFERENCES cfg.analyte',
    'REFERENCES "cfg"."analyte"',
)


def test_portal_clinical_schema_has_no_shell_tables() -> None:
    text = (SQL_PG / "portal_clinical_schema.sql").read_text(encoding="utf-8")
    hits = _SHELL_TABLE_DDL.findall(text)
    assert not hits, f"portal shell tables must not be in Omics schema: {hits}"


def test_legacy_cross_has_no_shell_fks_and_customers_point_at_organizations() -> None:
    path = SQL_PG / "legacy_cross_schema_fks.sql"
    text = path.read_text(encoding="utf-8")
    shell_hits = _FORBIDDEN_FK.findall(text)
    assert not shell_hits, f"Omics FKs must not reference portal shell tables: {shell_hits}"

    offenders: list[str] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        upper = line.upper()
        if "REFERENCES" not in upper:
            continue
        if any(token in line for token in _ALLOWED_CROSS_SCHEMA):
            continue
        if 'REFERENCES "RBAC".' in line or 'REFERENCES "Contract".' in line:
            offenders.append(f"{path.name}:{line_no}: {line.strip()}")
        if 'REFERENCES "Meta".' in line or 'REFERENCES "Onboarding".' in line:
            offenders.append(f"{path.name}:{line_no}: {line.strip()}")
    assert not offenders, "Omics must not add FKs into platform schemas:\n" + "\n".join(offenders)
    assert 'FK_Customers_Organizations_ID' in text
    assert "REFERENCES portal.\"Organizations\"" in text
