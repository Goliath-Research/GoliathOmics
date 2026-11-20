"""Published cfg.site documents reach instance bake as siteConfig."""

from __future__ import annotations

from typing import Any, Optional

from ops.study_start_queue import load_published_site_document


class _Db:
    def __init__(self, *, document: Any = None, error: Optional[Exception] = None, fallback: Any = None):
        self.document = document
        self.error = error
        self.fallback = fallback
        self.calls: list[tuple[str, tuple]] = []

    def _fetch_one(self, sql: str, params: tuple = ()):
        self.calls.append((sql, params))
        if "fn_published_site_document" in sql:
            if self.error is not None:
                raise self.error
            if self.document is None:
                return {"document": None}
            return {"document": self.document}
        return {"document": self.fallback}


def test_load_published_site_document_by_id() -> None:
    db = _Db(document={"actionConfig": {"alignment_qc": {"enabled": True}}})
    out = load_published_site_document(db, 42)
    assert out == {"actionConfig": {"alignment_qc": {"enabled": True}}}
    assert db.calls[0][1] == (42,)


def test_load_published_site_document_null_falls_back_to_file() -> None:
    db = _Db(document=None)
    assert load_published_site_document(db, 7) is None


def test_load_published_site_document_falls_back_to_cfg_site() -> None:
    db = _Db(error=RuntimeError("function does not exist"), fallback={"reference_genome": {"fasta": "/r.fa"}})
    out = load_published_site_document(db, 9)
    assert out == {"reference_genome": {"fasta": "/r.fa"}}
    assert any("cfg.site" in sql for sql, _ in db.calls)


def test_empty_object_is_treated_as_missing() -> None:
    db = _Db(document={})
    assert load_published_site_document(db, 1) is None
