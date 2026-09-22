from __future__ import annotations

from pathlib import Path
import sqlite3

import pandas as pd

from methyl_enricher import network_discovery


def test_run_network_discovery_writes_db_and_export(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "scan" / "run1"
    run_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "source": ["TP53", "BRCA1", "MYC"],
            "target": ["BRCA1", "MYC", "CDK2"],
            "score": [820.0, 610.0, 450.0],
        }
    ).to_csv(run_dir / "ppi_network_edges.csv", index=False)
    pd.DataFrame(
        {
            "Module": ["M1", "M2"],
            "Main_genes": ["TP53,BRCA1,MYC", "MYC,CDK2"],
        }
    ).to_csv(run_dir / "modules_ranked.csv", index=False)

    def _fake_fetch(**kwargs):
        del kwargs
        return pd.DataFrame({"source": ["TP53"], "target": ["BRCA1"], "score": [900.0]})

    monkeypatch.setattr(network_discovery, "fetch_string_edges", _fake_fetch)

    result = network_discovery.run_network_discovery(
        scan_roots=[run_dir.parent],
        methyl_enricher_home=tmp_path / "cache",
        project_name="proj",
        min_export_score=0.0,
    )
    assert result.db_path.is_file()
    assert result.export_path.is_file()
    exported = pd.read_csv(result.export_path)
    assert {"source", "target", "score"}.issubset(exported.columns)
    conn = sqlite3.connect(str(result.db_path))
    try:
        n_edges = conn.execute(
            "SELECT COUNT(*) FROM network_edge WHERE snapshot_id = ?",
            (result.snapshot_id,),
        ).fetchone()[0]
        assert int(n_edges) >= 2
    finally:
        conn.close()


def test_resolve_discovery_paths_uses_home_root(tmp_path: Path):
    paths = network_discovery.resolve_discovery_paths(tmp_path / "custom_home")
    assert str(paths.db_path).endswith(
        "custom_home/network_discovery/custom_network.sqlite"
    )
    assert str(paths.export_path).endswith(
        "custom_home/network_discovery/exports/local_edges.csv"
    )

