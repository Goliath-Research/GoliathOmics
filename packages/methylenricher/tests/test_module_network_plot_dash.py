import json

from methyl_enricher import module_network_plot


def _sample_graph_inputs():
    pathway_to_module_id = {"cell cycle": 0, "dna repair": 1}
    pathway_to_genes = {"cell cycle": {"TP53", "CDK1"}, "dna repair": {"BRCA1", "TP53"}}
    module_id_to_label = {0: "CellCycle", 1: "DNARepair"}
    return pathway_to_module_id, pathway_to_genes, module_id_to_label


def test_build_cytoscape_payload_schema_is_stable():
    p2m, p2g, mid2label = _sample_graph_inputs()
    G = module_network_plot.build_pathway_similarity_graph(
        p2m,
        p2g,
        similarity_threshold=0.1,
        module_id_to_label=mid2label,
    )
    payload = module_network_plot.build_cytoscape_payload_from_graph(G)
    assert {"nodes", "edges", "elements", "stylesheet"}.issubset(payload.keys())
    assert payload["nodes"]
    assert payload["elements"]
    node = payload["nodes"][0]
    assert "data" in node
    assert {"id", "label", "module", "module_id", "n_genes"}.issubset(node["data"].keys())


def test_write_network_plots_cytoscape_json_regression(tmp_path):
    p2m, p2g, mid2label = _sample_graph_inputs()
    module_network_plot.write_network_plots(
        output_dir=tmp_path,
        pathway_to_module_id=p2m,
        pathway_to_genes=p2g,
        merged_df=None,
        module_id_to_label=mid2label,
        similarity_threshold=0.1,
        network_plot="cytoscape",
    )
    json_path = tmp_path / "pathway_network_cytoscape.json"
    assert json_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert {"nodes", "edges"} == set(data.keys())
    assert data["nodes"]
    assert {"id", "label", "module", "n_genes"}.issubset(data["nodes"][0]["data"].keys())


def test_write_network_plots_dash_routes_to_launcher(monkeypatch, tmp_path):
    p2m, p2g, mid2label = _sample_graph_inputs()
    called = {}

    def _fake_launch_dashboard(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr("methyl_enricher.module_network_dash.launch_dashboard", _fake_launch_dashboard)
    module_network_plot.write_network_plots(
        output_dir=tmp_path,
        pathway_to_module_id=p2m,
        pathway_to_genes=p2g,
        merged_df=None,
        module_id_to_label=mid2label,
        similarity_threshold=0.1,
        network_plot="dash",
        dash_host="0.0.0.0",
        dash_port=9999,
        dash_open_browser=True,
    )
    assert called["host"] == "0.0.0.0"
    assert called["port"] == 9999
    assert called["open_browser"] is True
    assert called["pathway_elements"]
