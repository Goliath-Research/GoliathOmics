import argparse

from methyl_enricher.cli import _apply_enricher_config_to_args
from methyl_enricher.config import EnricherStepConfig


def _base_args():
    return argparse.Namespace(
        input=None,
        outdir="results",
        network_plot=None,
        dash_host="127.0.0.1",
        dash_port=8050,
        dash_open_browser=False,
        network_refinement_enabled=False,
        network_refinement_source="string_api",
        network_refinement_local_edges_file=None,
        network_refinement_cache_path=None,
        network_refinement_score_threshold=400.0,
        network_refinement_community_method="louvain",
        network_refinement_min_component_size=2,
        network_refinement_weight_in_final_score=0.3,
        network_refinement_hub_ranking_mode="signal_weighted",
        network_refinement_hub_disease_boost=0.0,
        network_refinement_hub_w_degree=None,
        network_refinement_hub_w_betweenness=None,
        network_refinement_hub_w_closeness=None,
    )


def test_apply_nested_network_refinement_config():
    args = _base_args()
    cfg = EnricherStepConfig.model_validate(
        {
            "network_refinement": {
                "enabled": True,
                "source": "local_edges",
                "local_edges_file": "/tmp/edges.csv",
                "cache_path": "/tmp/shared-cache",
                "score_threshold": 650,
                "community_method": "connected_components",
                "min_component_size": 3,
                "weight_in_final_score": 0.45,
            }
        }
    )
    _apply_enricher_config_to_args(args, cfg)
    assert args.network_refinement_enabled is True
    assert args.network_refinement_source == "local_edges"
    assert args.network_refinement_local_edges_file == "/tmp/edges.csv"
    assert args.network_refinement_cache_path == "/tmp/shared-cache"
    assert args.network_refinement_score_threshold == 650
    assert args.network_refinement_community_method == "connected_components"
    assert args.network_refinement_min_component_size == 3
    assert args.network_refinement_weight_in_final_score == 0.45


def test_apply_network_refinement_respects_non_default_cli_values():
    args = _base_args()
    args.network_refinement_source = "local_edges"
    args.network_refinement_weight_in_final_score = 0.7
    cfg = EnricherStepConfig.model_validate(
        {
            "network_refinement": {
                "source": "string_api",
                "weight_in_final_score": 0.2,
            }
        }
    )
    _apply_enricher_config_to_args(args, cfg)
    assert args.network_refinement_source == "local_edges"
    assert args.network_refinement_weight_in_final_score == 0.7


def test_apply_dash_runtime_config():
    args = _base_args()
    cfg = EnricherStepConfig.model_validate(
        {
            "network_plot": "dash",
            "dash_host": "0.0.0.0",
            "dash_port": 9999,
            "dash_open_browser": True,
        }
    )
    _apply_enricher_config_to_args(args, cfg)
    assert args.network_plot == "dash"
    assert args.dash_host == "0.0.0.0"
    assert args.dash_port == 9999
    assert args.dash_open_browser is True
