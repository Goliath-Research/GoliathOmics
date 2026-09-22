"""
Dash + dash_cytoscape interactive viewer for module and optional PPI networks.
"""

from __future__ import annotations

import logging
import threading
import webbrowser
from typing import Dict, List, Optional, Tuple

from .module_network_plot import cytoscape_default_stylesheet

logger = logging.getLogger(__name__)


def _split_elements(elements: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    nodes = [e for e in elements if "source" not in e.get("data", {})]
    edges = [e for e in elements if "source" in e.get("data", {})]
    return nodes, edges


def _available_modules(elements: List[Dict]) -> List[str]:
    modules = {
        str(e.get("data", {}).get("module", "Other"))
        for e in elements
        if "source" not in e.get("data", {})
    }
    return sorted(m for m in modules if m)


def _filter_elements(
    elements: List[Dict],
    selected_module: str,
    edge_threshold: float,
) -> List[Dict]:
    nodes, edges = _split_elements(elements)
    if selected_module and selected_module != "__all__":
        keep_nodes = {
            str(n.get("data", {}).get("id"))
            for n in nodes
            if str(n.get("data", {}).get("module", "Other")) == selected_module
        }
    else:
        keep_nodes = {str(n.get("data", {}).get("id")) for n in nodes}

    kept_edges = []
    for edge in edges:
        data = edge.get("data", {})
        src = str(data.get("source"))
        dst = str(data.get("target"))
        weight = float(data.get("weight", 0.0) or 0.0)
        if src in keep_nodes and dst in keep_nodes and weight >= edge_threshold:
            kept_edges.append(edge)

    visible_nodes = [n for n in nodes if str(n.get("data", {}).get("id")) in keep_nodes]
    return visible_nodes + kept_edges


def launch_dashboard(
    *,
    pathway_elements: List[Dict],
    pathway_stylesheet: Optional[List[Dict]] = None,
    ppi_elements: Optional[List[Dict]] = None,
    ppi_stylesheet: Optional[List[Dict]] = None,
    host: str = "127.0.0.1",
    port: int = 8050,
    open_browser: bool = False,
) -> None:
    """Launch a local Dash Cytoscape server."""
    try:
        from dash import Dash, Input, Output, State, dcc, html
        import dash_cytoscape as cyto
    except Exception as exc:
        raise ImportError(
            "Dash visualization requires optional dependencies: dash and dash-cytoscape."
        ) from exc

    pathway_stylesheet = pathway_stylesheet or cytoscape_default_stylesheet(show_labels=True)
    ppi_stylesheet = ppi_stylesheet or cytoscape_default_stylesheet(show_labels=True)
    ppi_elements = ppi_elements or []

    app = Dash(__name__)
    app.title = "MethylEnricher Network Viewer"

    app.layout = html.Div(
        [
            html.H3("MethylEnricher Cytoscape Viewer"),
            dcc.Tabs(
                id="dataset-tabs",
                value="pathway",
                children=[
                    dcc.Tab(label="Pathway network", value="pathway"),
                    dcc.Tab(label="PPI network", value="ppi", disabled=(len(ppi_elements) == 0)),
                ],
            ),
            html.Div(
                [
                    html.Label("Module filter"),
                    dcc.Dropdown(id="module-filter", clearable=False, value="__all__"),
                    dcc.Checklist(
                        id="show-labels",
                        options=[{"label": "Show labels", "value": "labels"}],
                        value=["labels"],
                        inline=True,
                    ),
                    html.Label("Minimum edge weight"),
                    dcc.Slider(id="edge-threshold", min=0.0, max=1.0, step=0.01, value=0.0),
                    html.Button("Reset layout", id="reset-layout", n_clicks=0),
                ],
                style={"maxWidth": "600px", "padding": "8px 0"},
            ),
            dcc.Store(id="pathway-elements-store", data=pathway_elements),
            dcc.Store(id="ppi-elements-store", data=ppi_elements),
            cyto.Cytoscape(
                id="cy",
                elements=pathway_elements,
                style={"width": "100%", "height": "80vh", "border": "1px solid #ddd"},
                layout={"name": "preset"},
                stylesheet=pathway_stylesheet,
            ),
        ]
    )

    @app.callback(
        Output("module-filter", "options"),
        Output("module-filter", "value"),
        Input("dataset-tabs", "value"),
        State("pathway-elements-store", "data"),
        State("ppi-elements-store", "data"),
    )
    def _update_module_options(dataset, pathway_data, ppi_data):
        elements = pathway_data if dataset == "pathway" else ppi_data
        modules = _available_modules(elements or [])
        options = [{"label": "All", "value": "__all__"}] + [
            {"label": m, "value": m} for m in modules
        ]
        return options, "__all__"

    @app.callback(
        Output("cy", "elements"),
        Output("cy", "stylesheet"),
        Output("cy", "layout"),
        Input("dataset-tabs", "value"),
        Input("module-filter", "value"),
        Input("show-labels", "value"),
        Input("edge-threshold", "value"),
        Input("reset-layout", "n_clicks"),
        State("pathway-elements-store", "data"),
        State("ppi-elements-store", "data"),
    )
    def _update_graph(dataset, module_value, labels_value, edge_threshold, n_clicks, pathway_data, ppi_data):
        elements = pathway_data if dataset == "pathway" else ppi_data
        elements = elements or []
        filtered = _filter_elements(
            elements=elements,
            selected_module=module_value or "__all__",
            edge_threshold=float(edge_threshold or 0.0),
        )
        show_labels = bool(labels_value and "labels" in labels_value)
        stylesheet = cytoscape_default_stylesheet(show_labels=show_labels)
        layout = {"name": "preset" if (n_clicks or 0) % 2 == 0 else "cose"}
        return filtered, stylesheet, layout

    if open_browser:
        url = f"http://{host}:{port}"
        threading.Timer(0.8, lambda: webbrowser.open_new_tab(url)).start()

    logger.info("Starting Dash Cytoscape viewer on http://%s:%s", host, port)
    app.run(host=host, port=int(port), debug=False)
