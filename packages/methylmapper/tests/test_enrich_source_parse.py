from methyl_mapper.bedtools_mapper import BedtoolsMapper


def test_parse_enrich_source_opentargets_token() -> None:
    use_grok, use_open_targets, use_disgenet = BedtoolsMapper._parse_enrich_source("opentargets")
    assert use_grok is False
    assert use_open_targets is True
    assert use_disgenet is False


def test_parse_enrich_source_combined_sources() -> None:
    use_grok, use_open_targets, use_disgenet = BedtoolsMapper._parse_enrich_source("grok+opentargets")
    assert use_grok is True
    assert use_open_targets is True
    assert use_disgenet is False


def test_parse_enrich_source_plant_traits_no_open_targets() -> None:
    use_grok, use_open_targets, use_disgenet = BedtoolsMapper._parse_enrich_source("plant_traits")
    assert use_grok is False
    assert use_open_targets is False
    assert use_disgenet is False
    assert BedtoolsMapper._is_plant_traits_source("plant_traits") is True
