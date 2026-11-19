"""Pipeline profile document type: a self-contained bundle of schemas/config/profile.schema.json."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from goliath_omics.seed import config_document_types as docs

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILES = sorted((REPO_ROOT / "workflow_engine/domain/profiles").glob("*.profile.json"))


def test_bundle_has_only_local_refs_that_resolve() -> None:
    schema = docs.pipeline_profile_schema()
    refs = set(re.findall(r'"\$ref": "([^"]*)"', json.dumps(schema)))
    assert refs and all(ref.startswith("#/$defs/") for ref in refs)
    assert all(ref[len("#/$defs/") :] in schema["$defs"] for ref in refs)
    assert "$schema" not in schema


def test_bundle_validates_profiles_like_the_source_schemas() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    referencing = pytest.importorskip("referencing")
    config = REPO_ROOT / "schemas/config"
    registry = referencing.Registry().with_resources(
        [(p.name, referencing.Resource.from_contents(json.loads(p.read_text()))) for p in config.glob("*.schema.json")]
    )
    source = jsonschema.Draft202012Validator(json.loads((config / "profile.schema.json").read_text()), registry=registry)
    bundled = jsonschema.Draft202012Validator(docs.pipeline_profile_schema())
    jsonschema.Draft202012Validator.check_schema(docs.pipeline_profile_schema())
    assert PROFILES
    for path in PROFILES:
        doc = json.loads(path.read_text())
        assert source.is_valid(doc) == bundled.is_valid(doc), path.name


def test_seed_registers_the_pipeline_profile_kind() -> None:
    calls: list[tuple[str, tuple]] = []

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def execute(self, sql, params=()):
            calls.append((sql, params))

        def fetchone(self):
            return (True,)

    class Conn:
        committed = False

        def cursor(self):
            return Cur()

        def commit(self):
            Conn.committed = True

    assert docs.seed(Conn()) is True
    assert Conn.committed
    upsert = next(p for s, p in calls if "wf_repo_upsert_data_type" in s)
    assert upsert[0] == "omics/PipelineProfile"
    assert json.loads(upsert[2])["title"] == "PipelineProfile"
    assert next(p for s, p in calls if s.startswith("SELECT cfg.sp_register_kind_document_type")) == ("omics/PipelineProfile",)
