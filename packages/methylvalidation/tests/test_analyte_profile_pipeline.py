"""Pipeline steps respect analyte profiles from primary_analyte."""

from methyl_utils.action_config_resolver import resolve_action_config
from methyl_utils.pipeline_config import ProjectConfig


def test_cfdna_profile_enables_fragmentomics_step():
    project = ProjectConfig.model_validate(
        {
            "project_name": "cfdna_min",
            "output_base": "/out",
            "group1": {"label": "h", "sample_paths": ["/s/h"]},
            "group2": {"label": "t", "sample_paths": ["/s/t"]},
            "regulatory": {"primary_analyte": "cfdna"},
        }
    )
    reg = project.get_regulatory_config()
    assert (
        resolve_action_config("fragmentomics", profile_action_config={}, regulatory=reg).get("enabled")
        is True
    )
    assert (
        resolve_action_config("validation", profile_action_config={}, regulatory=reg).get(
            "enforce_training_analyte_match"
        )
        is True
    )
