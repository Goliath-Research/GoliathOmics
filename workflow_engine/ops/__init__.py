"""Direct-DB operator helpers (catalog seed, workflow deploy, study lifecycle).

Shared by ``methyl-study-start`` (Admin CLI) and deploy/seed scripts.
Uses ``rest.db_client`` for PostgreSQL. Not exposed on
the worker-only REST gateway.
"""
