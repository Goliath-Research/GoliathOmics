"""
Network plot for pathway/module graph: build pathway similarity graph with node attributes,
export as interactive Plotly HTML (and optionally PyVis or Cytoscape.js).
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Set

import networkx as nx
import pandas as pd

logger = logging.getLogger(__name__)


def build_pathway_similarity_graph(
    pathway_to_module_id: Dict[str, int],
    pathway_to_genes: Dict[str, Set[str]],
    similarity_threshold: float,
    module_id_to_label: Dict[int, str],
    use_jaccard: bool = True,
):
    """
    Build a NetworkX graph of pathways with edges where Jaccard similarity >= threshold.
    Attach node attributes: module_label, n_genes, module_id.

    Returns:
        nx.Graph with nodes = pathways, edges = similarity above threshold.
    """
    from .pathway_graph import build_similarity_graph

    if not pathway_to_genes:
        return nx.Graph()

    G, _ = build_similarity_graph(
        pathway_to_genes,
        similarity_threshold=similarity_threshold,
        use_jaccard=use_jaccard,
    )

    for node in G.nodes():
        mid = pathway_to_module_id.get(node, -1)
        genes = pathway_to_genes.get(node, set())
        G.nodes[node]["module_id"] = mid
        G.nodes[node]["module_label"] = module_id_to_label.get(mid, "Other")
        G.nodes[node]["n_genes"] = len(genes)

    return G


def plot_pathway_network_plotly(
    G: nx.Graph,
    output_path: Path,
    layout: str = "spring",
    node_size_by: str = "n_genes",
) -> None:
    """
    Compute layout with NetworkX, build Plotly figure (nodes colored by module, sized by n_genes),
    write interactive HTML to output_path.
    """
    import plotly.graph_objects as go
    from plotly.colors import qualitative

    if not G.nodes():
        logger.warning("Empty graph; skipping Plotly network plot.")
        return

    if layout == "spring":
        pos = nx.spring_layout(G, seed=42, k=1.5)
    elif layout == "kamada_kawai":
        pos = nx.kamada_kawai_layout(G)
    else:
        pos = nx.spring_layout(G, seed=42, k=1.5)

    node_list = list(G.nodes())
    # Unique module labels for discrete legend mapping
    labels = sorted({G.nodes[n].get("module_label", "Other") for n in node_list})
    palette = qualitative.Plotly + qualitative.D3 + qualitative.Dark24
    label_to_color = {label: palette[i % len(palette)] for i, label in enumerate(labels)}

    x = [pos[n][0] for n in node_list]
    y = [pos[n][1] for n in node_list]
    sizes = [G.nodes[n].get(node_size_by, 5) for n in node_list]
    # Scale for visibility (e.g. 8–25)
    min_s, max_s = min(sizes) or 1, max(sizes) or 1
    if max_s > min_s:
        sizes = [8 + 17 * (s - min_s) / (max_s - min_s) for s in sizes]
    else:
        sizes = [12] * len(sizes)

    node_records = []
    for n, xv, yv, sv in zip(node_list, x, y, sizes):
        module_label = G.nodes[n].get("module_label", "Other")
        ng = G.nodes[n].get("n_genes", 0)
        node_records.append(
            {
                "node": n,
                "x": xv,
                "y": yv,
                "size": sv,
                "module": module_label,
                "hover": f"<b>{n}</b><br>Module: {module_label}<br>Genes: {ng}",
            }
        )

    edge_x, edge_y = [], []
    for u, v in G.edges():
        x0, y0 = pos[u]
        x1, y1 = pos[v]
        edge_x.extend([x0, x1, None])
        edge_y.extend([y0, y1, None])

    edge_trace = go.Scatter(
        x=edge_x,
        y=edge_y,
        line=dict(width=0.8, color="#888"),
        hoverinfo="none",
        mode="lines",
        showlegend=False,
        name="",
    )

    node_traces = []
    for label in labels:
        sub = [r for r in node_records if r["module"] == label]
        if not sub:
            continue
        node_traces.append(
            go.Scatter(
                x=[r["x"] for r in sub],
                y=[r["y"] for r in sub],
                mode="markers+text",
                marker=dict(
                    size=[r["size"] for r in sub],
                    color=label_to_color[label],
                    line=dict(width=0.5, color="#333333"),
                ),
                text=[label] * len(sub),
                textposition="top center",
                textfont=dict(size=9),
                hovertext=[r["hover"] for r in sub],
                hoverinfo="text",
                name=str(label),
                showlegend=True,
            )
        )

    fig = go.Figure(data=[edge_trace, *node_traces])
    fig.update_layout(
        showlegend=True,
        legend=dict(
            title="Module",
            itemsizing="constant",
            orientation="h",
            x=0.0,
            xanchor="left",
            y=1.02,
            yanchor="bottom",
            bgcolor="rgba(255,255,255,0.85)",
        ),
        title=dict(
            text="Pathway similarity network (nodes = pathways, colored by module)",
            x=0.5,
            xanchor="center",
            y=0.99,
            yanchor="top",
            pad=dict(b=16),
        ),
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        margin=dict(b=20, l=20, r=20, t=180),
        hovermode="closest",
        plot_bgcolor="white",
    )
    fig.write_html(str(output_path))
    logger.info(f"Wrote Plotly network plot: {output_path}")


def _compute_positions(G: nx.Graph, layout: str = "spring") -> Dict[str, tuple]:
    """Compute deterministic node positions for Cytoscape payloads."""
    if layout == "kamada_kawai":
        return nx.kamada_kawai_layout(G)
    return nx.spring_layout(G, seed=42, k=1.5)


def cytoscape_default_stylesheet(show_labels: bool = True) -> List[Dict]:
    """Default Cytoscape stylesheet shared by static and Dash views."""
    label_value = "data(label)" if show_labels else ""
    return [
        {
            "selector": "node",
            "style": {
                "label": label_value,
                "text-valign": "bottom",
                "text-margin-y": 4,
                "font-size": 10,
                "width": 20,
                "height": 20,
                "background-color": "#6fb1fc",
            },
        },
        {
            "selector": "edge",
            "style": {
                "width": "mapData(weight, 0, 1, 1, 4)",
                "line-color": "#ccc",
                "opacity": 0.8,
            },
        },
    ]


def build_cytoscape_payload_from_graph(
    G: nx.Graph,
    *,
    layout: str = "spring",
    include_positions: bool = True,
    show_labels: bool = True,
) -> Dict[str, List[Dict]]:
    """
    Build Cytoscape-compatible payload from a NetworkX graph.

    Returns dict with keys: nodes, edges, elements, stylesheet.
    """
    positions = _compute_positions(G, layout=layout) if include_positions and G.nodes() else {}
    node_ids = sorted(str(n) for n in G.nodes())

    nodes_cy: List[Dict] = []
    for node_id in node_ids:
        attrs = G.nodes[node_id]
        data = {
            "id": node_id,
            "label": str(attrs.get("label", node_id))[:80],
            "module": str(attrs.get("module_label", "Other")),
            "module_id": int(attrs.get("module_id", -1)),
            "n_genes": int(attrs.get("n_genes", 0)),
        }
        node_entry = {"data": data}
        if node_id in positions:
            px, py = positions[node_id]
            node_entry["position"] = {"x": float(px * 400), "y": float(py * 400)}
        nodes_cy.append(node_entry)

    edge_tuples = sorted((str(u), str(v)) for u, v in G.edges())
    edges_cy: List[Dict] = []
    for u, v in edge_tuples:
        w = float(G.edges[u, v].get("weight", 0.0))
        edges_cy.append({"data": {"source": u, "target": v, "weight": w}})

    stylesheet = cytoscape_default_stylesheet(show_labels=show_labels)
    return {
        "nodes": nodes_cy,
        "edges": edges_cy,
        "elements": nodes_cy + edges_cy,
        "stylesheet": stylesheet,
    }


def write_network_plots(
    output_dir: Path,
    pathway_to_module_id: Dict[str, int],
    pathway_to_genes: Dict[str, Set[str]],
    merged_df: pd.DataFrame,
    module_id_to_label: Dict[int, str],
    similarity_threshold: float,
    network_plot: str,
    ppi_elements: Optional[List[Dict]] = None,
    ppi_stylesheet: Optional[List[Dict]] = None,
    dash_host: str = "127.0.0.1",
    dash_port: int = 8050,
    dash_open_browser: bool = False,
) -> None:
    """
    Generate network plot(s) according to network_plot: 'plotly', 'pyvis', 'cytoscape', or 'all'.
    Skips if network_plot is None or 'none'.
    """
    if not network_plot or network_plot.lower() == "none":
        return

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    G = build_pathway_similarity_graph(
        pathway_to_module_id,
        pathway_to_genes,
        similarity_threshold,
        module_id_to_label,
    )
    if not G.nodes():
        return

    modes = ["plotly", "pyvis", "cytoscape"] if network_plot.lower() == "all" else [network_plot.lower()]
    skipped = []
    cy_payload = build_cytoscape_payload_from_graph(G, layout="spring", include_positions=True)

    for mode in modes:
        if mode == "plotly":
            out_path = output_dir / "pathway_network_plotly.html"
            plot_pathway_network_plotly(G, out_path)
        elif mode == "pyvis":
            try:
                _write_pyvis_html(G, output_dir / "pathway_network_pyvis.html")
            except ImportError:
                skipped.append("pyvis (install with: pip install pyvis)")
                logger.warning("PyVis not installed; skipping pathway_network_pyvis.html. Install with: pip install pyvis")
        elif mode == "cytoscape":
            _write_cytoscape_html(cy_payload, output_dir)
        elif mode == "dash":
            from .module_network_dash import launch_dashboard
            launch_dashboard(
                pathway_elements=cy_payload["elements"],
                pathway_stylesheet=cy_payload["stylesheet"],
                ppi_elements=ppi_elements,
                ppi_stylesheet=ppi_stylesheet,
                host=dash_host,
                port=int(dash_port),
                open_browser=bool(dash_open_browser),
            )

    if skipped and network_plot.lower() == "all":
        logger.info("Network plots: some formats skipped: %s. Generated: plotly, cytoscape (PyVis optional).", ", ".join(skipped))


def _write_pyvis_html(G: nx.Graph, output_path: Path) -> None:
    """Export graph to a single HTML file using PyVis (vis.js)."""
    from pyvis.network import Network

    net = Network(
        height="600px",
        width="100%",
        directed=False,
        notebook=False,
    )
    net.from_nx(G)
    net.set_options("""
    var options = {
      "nodes": {
        "font": { "size": 12 },
        "scaling": { "label": { "enabled": true } }
      },
      "physics": {
        "enabled": true,
        "solver": "forceAtlas2Based",
        "stabilization": { "iterations": 150 }
      }
    }
    """)
    net.write_html(str(output_path))
    logger.info(f"Wrote PyVis network plot: {output_path}")


def _write_cytoscape_html(cy_payload: Dict[str, List[Dict]], output_dir: Path) -> None:
    """Export graph to Cytoscape.js HTML + JSON."""
    import json

    nodes_cy = cy_payload.get("nodes", [])
    edges_cy = cy_payload.get("edges", [])
    stylesheet = cy_payload.get("stylesheet", cytoscape_default_stylesheet())

    json_path = output_dir / "pathway_network_cytoscape.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"nodes": nodes_cy, "edges": edges_cy}, f, indent=2)

    html = _cytoscape_html_template(nodes_cy, edges_cy, stylesheet)
    html_path = output_dir / "pathway_network_cytoscape.html"
    html_path.write_text(html, encoding="utf-8")
    logger.info(f"Wrote Cytoscape.js network: {html_path}, {json_path}")


def _cytoscape_html_template(nodes_cy: List[Dict], edges_cy: List[Dict], stylesheet: List[Dict]) -> str:
    """Single HTML that loads Cytoscape.js from CDN and renders the graph."""
    import json

    data_js = json.dumps({"nodes": nodes_cy, "edges": edges_cy})
    style_js = json.dumps(stylesheet)
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Pathway network (Cytoscape.js)</title>
  <script src="https://unpkg.com/cytoscape@3.28.1/dist/cytoscape.min.js"></script>
  <style>
    body {{ font-family: sans-serif; margin: 0; }}
    #cy {{ width: 100%; height: 90vh; }}
  </style>
</head>
<body>
  <h2>Pathway similarity network</h2>
  <div id="cy"></div>
  <script>
    var data = {data_js};
    var cy = cytoscape({{
      container: document.getElementById('cy'),
      elements: data.nodes.concat(data.edges),
      style: {style_js},
      layout: {{ name: 'preset' }}
    }});
  </script>
</body>
</html>
"""
