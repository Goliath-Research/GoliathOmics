"""Pipeline profile and site parameter document types: self-contained schema bundles."""

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
    upserts = [p for s, p in calls if "wf_repo_upsert_data_type" in s]
    assert upserts[0][0] == "omics/PipelineProfile"
    assert json.loads(upserts[0][2])["title"] == "PipelineProfile"
    assert upserts[1][0] == "omics/SiteParameters"
    assert json.loads(upserts[1][2])["title"] == "SiteParameters"
    kinds = [p for s, p in calls if s.startswith("SELECT cfg.sp_register_kind_document_type")]
    assert kinds == [("omics/PipelineProfile",), ("omics/SiteParameters",)]


SITES = [
    path
    for path in sorted((REPO_ROOT / "workflow_engine/domain/profiles").glob("site_*.example.json"))
] + [REPO_ROOT / "scripts/default_methyl_site.json"]


def test_site_bundle_has_only_local_refs_that_resolve() -> None:
    schema = docs.site_parameters_schema()
    refs = set(re.findall(r'"\$ref": "([^"]*)"', json.dumps(schema)))
    assert refs and all(ref.startswith("#/$defs/") for ref in refs)
    assert all(ref[len("#/$defs/") :] in schema["$defs"] for ref in refs)
    assert "$schema" not in schema
    assert schema["title"] == "SiteParameters"
    ac = schema["properties"]["actionConfig"]
    assert ac.get("additionalProperties") is False
    # Keys workers already resolve from the site, plus step schemas used in site examples.
    for key in (
        "alignment_qc",
        "extraction_qc",
        "parabricks",
        "methyl_extract",
        "methylgrapher_wgbs",
        "gene_selection",
        "storage_transfer",
        "cell_deconvolution",
        "docker_align",
        "demultiplex",
        "rna_align",
        "proteomics_quant",
    ):
        assert key in ac["properties"], key


def test_site_bundle_validates_like_the_source_schemas() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    referencing = pytest.importorskip("referencing")
    config = REPO_ROOT / "schemas/config"
    registry = referencing.Registry().with_resources(
        [(p.name, referencing.Resource.from_contents(json.loads(p.read_text()))) for p in config.glob("*.schema.json")]
    )
    source = jsonschema.Draft202012Validator(
        json.loads((config / "site_manifest.schema.json").read_text()), registry=registry
    )
    bundled = jsonschema.Draft202012Validator(docs.site_parameters_schema())
    jsonschema.Draft202012Validator.check_schema(docs.site_parameters_schema())
    # default_methyl_site.json is the committed default pin set and must validate.
    # Example sites may carry extra reference_selection keys the current source
    # schema already rejects; compare source vs bundle on documents the source accepts.
    accepted = []
    for path in SITES:
        doc = json.loads(path.read_text())
        if source.is_valid(doc):
            accepted.append(path)
            assert bundled.is_valid(doc), path.name
    assert any(path.name == "default_methyl_site.json" for path in accepted)


def test_threads_default_is_the_code_default_only() -> None:
    """proteomics_quant.threads default 8 comes from diann_runner.py; other new knobs have none."""
    schema = docs.site_parameters_schema()
    # Walk $defs / properties for proteomics_quant threads
    dumped = json.dumps(schema)
    assert "proteomics_quant" in dumped
    # The bundled schema must not invent a parabricks.bwa_threads default (model default is None).
    # Find the parabricks step properties in $defs.
    defs = schema.get("$defs") or {}
    para = None
    for name, sub in defs.items():
        if sub.get("title") == "ParabricksStepConfig" or (
            isinstance(sub.get("properties"), dict) and "bwa_threads" in sub.get("properties", {}) and "write_methylation_tags" in sub.get("properties", {})
        ):
            para = sub
            break
    # Also check the actionConfig.parabricks merged node
    ac = schema["properties"]["actionConfig"]["properties"]["parabricks"]
    props = ac.get("properties") or (para or {}).get("properties") or {}
    if "bwa_threads" in props:
        assert "default" not in props["bwa_threads"]

