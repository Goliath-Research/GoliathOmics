"""Action catalog includes split detector workflow actions."""

from methyl_worker.action_catalog import ACTION_CATALOG, PROJECT_ACTION_CONFIG_KEYS


def test_split_workflow_actions_registered():
    names = {e.action_name for e in ACTION_CATALOG}
    assert "pipeline.dmp_select" in names
    assert "pipeline.gene_select" in names
    assert "validation.biomarker_filter" in names


def test_action_config_keys_include_split_sections():
    assert "dmp_selection" in PROJECT_ACTION_CONFIG_KEYS
    assert "gene_selection" in PROJECT_ACTION_CONFIG_KEYS


def test_split_actions_have_dedicated_task_schema_specs():
    from methyl_worker.task_schema_registry import find_task_schema_spec

    for action_name, input_class, output_class in (
        ("pipeline.dmp_select", "DmpSelectTaskInput", "DmpSelectTaskOutput"),
        ("pipeline.gene_select", "GeneSelectTaskInput", "GeneSelectTaskOutput"),
        ("pipeline.gene_feature_select", "GeneFeatureSelectTaskInput", "GeneFeatureSelectTaskOutput"),
        ("validation.biomarker_filter", "BiomarkerFilterTaskInput", "BiomarkerFilterTaskOutput"),
    ):
        spec = find_task_schema_spec(action_name)
        assert spec is not None, action_name
        assert spec.input_class == input_class
        assert spec.output_class == output_class
        assert spec.input_filename == f"{action_name.replace('.', '_')}.input.schema.json"
        assert spec.output_filename == f"{action_name.replace('.', '_')}.output.schema.json"
