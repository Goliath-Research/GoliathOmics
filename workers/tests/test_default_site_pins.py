"""goliath-test site seed pins the linear reference and the Clara image."""

from __future__ import annotations

import json
from pathlib import Path


def test_default_methyl_site_pins_ensembl_116_and_parabricks_image() -> None:
    site_path = Path(__file__).resolve().parents[2] / "scripts" / "default_methyl_site.json"
    site = json.loads(site_path.read_text(encoding="utf-8"))
    assert site["reference_selection"]["linear"] == "linear/GRCh38/ensembl-116"
    assert site["reference_genome"]["fasta"].endswith(
        "linear/GRCh38/ensembl-116/Homo_sapiens.GRCh38.dna.primary_assembly.fa"
    )
    image = "nvcr.io/nvidia/clara/clara-parabricks:4.7.0-1"
    assert site["actionConfig"]["parabricks"]["image"] == image
    assert site["parabricks"]["image"] == image
    assert site["actionConfig"]["parabricks"]["engine"] == "parabricks"


def test_grch38_site_example_pins_the_same_reference_and_image() -> None:
    site_path = (
        Path(__file__).resolve().parents[2]
        / "workflow_engine"
        / "domain"
        / "profiles"
        / "site_grch38.example.json"
    )
    site = json.loads(site_path.read_text(encoding="utf-8"))
    assert "ensembl-116" in site["reference_genome"]["fasta"]
    assert site["reference_selection"]["linear"] == "linear/GRCh38/ensembl-116"
    assert (
        site["actionConfig"]["parabricks"]["image"]
        == "nvcr.io/nvidia/clara/clara-parabricks:4.7.0-1"
    )
