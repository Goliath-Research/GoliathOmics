"""Admin-tier study lifecycle tooling (domain-aware; not part of the agnostic gateway).

Uses ``rest.db_client`` / ``open_gateway_db`` so MSSQL and PostgreSQL share one API.
Lifecycle helpers live in ``ops``; this package is the ``methyl-study-start`` CLI entry.
"""
