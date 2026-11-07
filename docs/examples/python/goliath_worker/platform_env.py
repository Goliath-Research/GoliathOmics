"""Minimal stub for GoliathApp `from goliath_worker.platform_env import resolve_database_url_override`."""

from __future__ import annotations


def resolve_database_url_override() -> str | None:
    """Return None so `rest.connection` uses PG* / POSTGRES_* libpq variables."""
    return None
