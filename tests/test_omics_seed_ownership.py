"""Seeds: omics namespace owner, one owning class per action, derivation step hand-off."""

from __future__ import annotations

from typing import Any

from goliath_omics.seed import meta_action_methods, semantic_catalog


class _Cursor:
    def __init__(self, conn: "_Conn") -> None:
        self.conn = conn
        self._last: Any = None

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.conn.calls.append((" ".join(sql.split()), params))
        self._last = self.conn.answer(sql, params)

    def fetchone(self) -> Any:
        return self._last if not isinstance(self._last, list) else (self._last[0] if self._last else None)

    def fetchall(self) -> list:
        return self._last if isinstance(self._last, list) else []


class _Conn:
    def __init__(self, answer) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.answer = answer
        self.commits = 0

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self.commits += 1


def _semantic_answer(gaps: int = 0):
    def answer(sql: str, params: tuple) -> Any:
        if "fn_semantic_entity_class_read_gaps" in sql:
            return (gaps,)
        if 'FROM "Meta"."Classes"' in sql:
            return (100 + len(params[0]),)
        return (1,)

    return answer


def test_semantic_seed_sets_omics_namespace_owner() -> None:
    conn = _Conn(_semantic_answer())
    semantic_catalog.seed(conn)
    first_sql, first_params = conn.calls[0]
    assert "INSERT INTO portal.semantic_namespace" in first_sql
    assert "owner_app = EXCLUDED.owner_app" in first_sql
    assert first_params == ("omics", "omics", "goliath_omics")


def test_study_actions_bind_to_the_study_entity_class() -> None:
    from goliath_omics.semantic.catalog import SEMANTIC_CATALOG

    study = next(e for e in SEMANTIC_CATALOG if e.code == "study")
    assert study.meta_class_name == "omics/Study"
    for entity_code in ("pipeline", "validation", "context"):
        assert meta_action_methods.ENTITY_META_CLASS[entity_code] == study.meta_class_name


def test_action_methods_skip_actions_a_platform_class_owns() -> None:
    def answer(sql: str, params: tuple) -> Any:
        if "LIKE 'platform/%%'" in sql:
            return (params[0].startswith("workflow."),)
        return ("ok",)

    conn = _Conn(answer)
    meta_action_methods.seed(conn)
    upserts = [p[2] for s, p in conn.calls if "sp_meta_upsert_action_method" in s]
    assert upserts and not any(name.startswith("workflow.") for name in upserts)
    assert any(name.startswith("pipeline.") for name in upserts)


def test_derive_methods_reports_rows_needing_attention() -> None:
    issue = ("sample.x", "omics/Sample", "ambiguous", 0, "omics.sample", "mode: data type exists")

    def answer(sql: str, params: tuple) -> Any:
        if "to_regprocedure" in sql:
            return (True,)
        if "sp_derive_action_methods('omics', true)" in sql:
            return [issue]
        return None

    conn = _Conn(answer)
    assert meta_action_methods.derive_methods(conn) == [issue]
    assert conn.commits == 1


def test_derive_methods_is_a_no_op_before_the_platform_step_exists() -> None:
    conn = _Conn(lambda sql, params: (False,))
    assert meta_action_methods.derive_methods(conn) == []
    assert not any("sp_derive_action_methods('omics'" in s for s, _ in conn.calls)


def test_remove_platform_duplicates_deletes_only_the_reviewed_count() -> None:
    dups = [(64, "omics/Study", "const_bool", "workflow.const_bool"), (2, "omics/LabBatch", "stock.adjust", "inv.stock.adjust")]

    def answer(sql: str, params: tuple) -> Any:
        return dups if "LIKE 'omics/%%'" in sql else None

    import pytest

    conn = _Conn(answer)
    with pytest.raises(SystemExit):
        meta_action_methods.remove_platform_duplicates(conn, expect=3)
    assert not any(s.startswith("DELETE") for s, _ in conn.calls)

    conn = _Conn(answer)
    assert meta_action_methods.remove_platform_duplicates(conn, expect=2) == dups
    deletes = [p for s, p in conn.calls if s.startswith("DELETE")]
    assert deletes == [(64,), (2,)]
    assert conn.commits == 1


def test_entities_stored_outside_meta_map_to_their_owning_class() -> None:
    from goliath_omics.semantic.catalog import SEMANTIC_CATALOG

    owners = {e.code: e.owner_class_name for e in SEMANTIC_CATALOG if e.owner_class_name}
    assert owners == {"group": "omics/Group", "lab": "omics/Lab", "lab_reagent": "platform/Lot"}
    conn = _Conn(_semantic_answer())
    semantic_catalog.seed(conn)
    looked_up = [p[0] for s, p in conn.calls if 'FROM "Meta"."Classes"' in s]
    assert {"omics/Group", "omics/Lab", "platform/Lot"} <= set(looked_up)
    registered = {p[1]: p[8] for s, p in conn.calls if "sp_semantic_register_entity" in s}
    assert registered["lab_reagent"] == 100 + len("platform/Lot")


def test_semantic_seed_grants_class_read_and_fails_on_access_gaps() -> None:
    import pytest

    conn = _Conn(_semantic_answer())
    semantic_catalog.seed(conn)
    grants = [p for s, p in conn.calls if "sp_grant_semantic_entity_class_read" in s and "SELECT *" in s]
    assert grants == [("omics",)]
    assert conn.commits == 1

    failing = _Conn(_semantic_answer(gaps=2))
    with pytest.raises(RuntimeError, match="would lose access"):
        semantic_catalog.seed(failing)
    assert failing.commits == 0
