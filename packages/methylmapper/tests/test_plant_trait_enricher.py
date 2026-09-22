"""Offline plant_traits enrich_source (no Open Targets)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from methyl_mapper.bedtools_mapper import BedtoolsMapper
from methyl_mapper.cli import normalize_enrich_source
from methyl_mapper.plant_trait_enricher import PlantTraitEnricher

DEMO_TSV = (
    Path(__file__).resolve().parents[3]
    / "docs/examples/samd/plant-abiotic-stress/data/arabidopsis_drought_gene_traits.tsv"
)


def _mapper_without_bedtools(**kwargs) -> BedtoolsMapper:
    """Construct BedtoolsMapper without requiring a host bedtools binary (CI agents)."""
    with patch("methyl_mapper.bedtools_mapper.subprocess.run") as mock_run:
        mock_run.return_value = None
        return BedtoolsMapper(**kwargs)


def test_normalize_enrich_source_plant_traits() -> None:
    assert normalize_enrich_source("plant_traits") == "plant_traits"
    assert normalize_enrich_source("plant-traits") == "plant_traits"
    with pytest.raises(Exception):
        normalize_enrich_source("plant_traits+opentargets")


def test_parse_enrich_source_plant_traits_disables_human_apis() -> None:
    assert BedtoolsMapper._is_plant_traits_source("plant_traits") is True
    use_grok, use_ot, use_dg = BedtoolsMapper._parse_enrich_source("plant_traits")
    assert (use_grok, use_ot, use_dg) == (False, False, False)


def test_plant_trait_enricher_joins_demo_tsv() -> None:
    assert DEMO_TSV.is_file()
    enricher = PlantTraitEnricher(DEMO_TSV, disease_term="drought")
    df = pd.DataFrame({"gene_name": ["AT5G52310", "AT9G99999", "dreb2a"]})
    # AGI IDs are case-insensitive; symbol-only rows are not in the demo table.
    out = enricher.enrich_gene_dataframe(df, gene_column="gene_name")
    hit = out.loc[out["gene_name"] == "AT5G52310"].iloc[0]
    assert bool(hit["disease_associated"])
    assert hit["disease_source"] == "plant_traits"
    assert "drought" in str(hit["disease_functional_role"]).lower()
    assert not bool(out.loc[out["gene_name"] == "AT9G99999", "disease_associated"].iloc[0])
    assert "disease_score" in out.columns


def test_bedtools_mapper_uses_plant_trait_enricher(tmp_path: Path) -> None:
    """Smoke: enrich_disease + plant_traits never constructs GeneDiseaseEnricher."""
    gtf = tmp_path / "tiny.gtf"
    gtf.write_text(
        '1\ttest\tgene\t1\t10\t.\t+\t.\tgene_id "g1"; gene_name "AT5G52310";\n',
        encoding="utf-8",
    )
    mapper = _mapper_without_bedtools(
        gene_gtf=gtf,
        enrich_disease=True,
        enrich_source="plant_traits",
        plant_traits_path=DEMO_TSV,
        disease_term="drought",
        optimize_dmps=False,
    )
    assert isinstance(mapper.disease_enricher, PlantTraitEnricher)
    assert not isinstance(mapper.disease_enricher, type(None))


def test_shared_enrichment_payload_path_works_for_plant_traits(tmp_path: Path) -> None:
    """Standard chromosome-combined path calls _build/_apply_enrichment_payload."""
    gtf = tmp_path / "tiny.gtf"
    gtf.write_text(
        '1\ttest\tgene\t1\t10\t.\t+\t.\tgene_id "g1"; gene_name "AT5G52310";\n',
        encoding="utf-8",
    )
    mapper = _mapper_without_bedtools(
        gene_gtf=gtf,
        enrich_disease=True,
        enrich_source="plant_traits",
        plant_traits_path=DEMO_TSV,
        disease_term="drought",
        optimize_dmps=False,
    )
    frames = [pd.DataFrame({"gene_name": ["AT5G52310", "AT0G00000"]})]
    payload = mapper._build_shared_enrichment_payload(frames, "gene_name")
    assert payload is not None
    assert payload["merged"]["AT5G52310"]["associated"] is True
    assert payload["merged"]["AT5G52310"]["source"] == "plant_traits"

    applied = mapper._apply_shared_enrichment_payload(frames[0], "gene_name", payload)
    assert bool(applied.loc[0, "disease_associated"])
    assert applied.loc[0, "disease_source"] == "plant_traits"
    assert not bool(applied.loc[1, "disease_associated"])
