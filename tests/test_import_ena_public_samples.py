"""Manifest and catalog checks for the public ENA sample import."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from goliath_omics.semantic.catalog import SEMANTIC_REQUESTS

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "schemas/import/ena_public_samples.json"


def _load_script():
    path = REPO_ROOT / "scripts/import_ena_public_samples.py"
    spec = importlib.util.spec_from_file_location("import_ena_public_samples", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_patient_semantic_requests_bind_official_functions() -> None:
    by_code = {r.request_code: r for r in SEMANTIC_REQUESTS}
    assert by_code["omics.patient.create"].target_name == "omics_clinical.fn_semantic_patient_create"
    assert by_code["omics.patient.update"].target_name == "omics_clinical.fn_semantic_patient_update"
    assert by_code["omics.patient.list"].target_name == "omics_clinical.fn_semantic_patient_list"


def test_manifest_records_sources_and_leaves_unknown_fields_empty() -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert data["institution"]["name"] == "ENA"
    assert data["institution"]["official_name"] == "European Nucleotide Archive"
    assert data["institution"]["source_url"].startswith("https://www.ebi.ac.uk/ena")
    sample = next(s for s in data["samples"] if s["run_accession"] == "SRR28293403")
    assert sample["sample_name"] == "canary-SRR28293403-linear-subset"
    assert sample["ena_sample_title"] == "WGBS-HG00621-Rep1"
    patient = sample["patient"]
    assert patient["external_id"] == "HG00621"
    assert patient["name"] == "HG00621"
    assert patient["sex"] == "M"
    assert patient["year_born"] is None
    assert patient["race"] is None
    assert patient["ethnicity"] is None
    sex_src = patient["field_sources"]["sex"]["source_url"]
    assert "biosamples/samples/SAME123082" in sex_src
    assert patient["igsr_population"]["value"] == "Han Chinese South"
    assert "internationalgenome.org" in patient["igsr_population"]["source_url"]


def test_load_manifest_rejects_unsourced_race() -> None:
    mod = _load_script()
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    data["samples"][0]["patient"]["race"] = "invented"
    data["samples"][0]["patient"]["field_sources"]["race"]["value"] = None
    tmp = MANIFEST.parent / "ena_public_samples.bad.json"
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        try:
            mod.load_manifest(tmp)
        except SystemExit:
            return
        raise AssertionError("expected SystemExit for unsourced race")
    finally:
        tmp.unlink(missing_ok=True)
