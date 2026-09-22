"""
Custom network discovery pipeline for MethylEnricher.

This module discovers candidate edges from existing enrichment outputs,
annotates novelty against STRING, stores snapshots in SQLite, and exports
curated edge lists compatible with network_refinement.source=local_edges.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
import sqlite3
import uuid
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import pandas as pd

from .ppi_network import fetch_string_edges, normalize_gene_symbols

DISCOVERY_SUBDIR = "network_discovery"
DEFAULT_DB_NAME = "custom_network.sqlite"
DEFAULT_EXPORTS_DIR = "exports"
DEFAULT_EXPORT_NAME = "local_edges.csv"


@dataclass
class DiscoveryPaths:
    db_path: Path
    export_path: Path


@dataclass
class DiscoveryResult:
    snapshot_id: str
    edges_df: pd.DataFrame
    module_membership_df: pd.DataFrame
    db_path: Path
    export_path: Path


def resolve_discovery_paths(
    methyl_enricher_home: str | Path,
    *,
    db_path: Optional[str | Path] = None,
    export_path: Optional[str | Path] = None,
) -> DiscoveryPaths:
    root = Path(str(methyl_enricher_home)).expanduser().resolve() / DISCOVERY_SUBDIR
    resolved_db = Path(db_path).expanduser().resolve() if db_path else (root / DEFAULT_DB_NAME)
    resolved_export = Path(export_path).expanduser().resolve() if export_path else (root / DEFAULT_EXPORTS_DIR / DEFAULT_EXPORT_NAME)
    return DiscoveryPaths(db_path=resolved_db, export_path=resolved_export)


def _canonical_edge(a: str, b: str) -> Tuple[str, str]:
    aa = str(a or "").strip().upper()
    bb = str(b or "").strip().upper()
    return (aa, bb) if aa <= bb else (bb, aa)


def _parse_gene_list_field(raw: object) -> List[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    return [g for g in normalize_gene_symbols([x.strip() for x in text.split(",")]) if g]


def _collect_edge_artifacts(scan_roots: Sequence[Path]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    edge_rows: List[Dict[str, object]] = []
    module_rows: List[Dict[str, object]] = []
    for root in scan_roots:
        if not root.exists():
            continue
        for edge_csv in root.rglob("ppi_network_edges.csv"):
            try:
                edges_df = pd.read_csv(edge_csv)
            except Exception:
                continue
            if edges_df.empty:
                continue
            for row in edges_df.itertuples(index=False):
                src = str(getattr(row, "source", "")).strip().upper()
                dst = str(getattr(row, "target", "")).strip().upper()
                if not src or not dst or src == dst:
                    continue
                a, b = _canonical_edge(src, dst)
                score = float(pd.to_numeric(getattr(row, "score", 0.0), errors="coerce") or 0.0)
                edge_rows.append(
                    {
                        "gene_a": a,
                        "gene_b": b,
                        "observed_score": score,
                        "source_file": str(edge_csv),
                        "source_run": str(edge_csv.parent),
                        "evidence_type": "ppi_edge",
                    }
                )
            modules_csv = edge_csv.parent / "modules_ranked.csv"
            if modules_csv.exists():
                try:
                    modules_df = pd.read_csv(modules_csv)
                except Exception:
                    modules_df = pd.DataFrame()
                if not modules_df.empty and "Main_genes" in modules_df.columns:
                    for m_row in modules_df.itertuples(index=False):
                        genes = _parse_gene_list_field(getattr(m_row, "Main_genes", ""))
                        module_label = str(getattr(m_row, "Module", "unknown")).strip() or "unknown"
                        for gene in genes:
                            module_rows.append(
                                {
                                    "module_label": module_label,
                                    "gene": gene,
                                    "source_file": str(modules_csv),
                                    "source_run": str(modules_csv.parent),
                                }
                            )
                        for g1, g2 in combinations(sorted(set(genes)), 2):
                            a, b = _canonical_edge(g1, g2)
                            edge_rows.append(
                                {
                                    "gene_a": a,
                                    "gene_b": b,
                                    "observed_score": 0.0,
                                    "source_file": str(modules_csv),
                                    "source_run": str(modules_csv.parent),
                                    "evidence_type": "module_copresence",
                                }
                            )
    return pd.DataFrame(edge_rows), pd.DataFrame(module_rows)


def _build_edge_table(
    evidence_df: pd.DataFrame,
    *,
    string_edges_df: pd.DataFrame,
    refinement_score_threshold: float,
) -> pd.DataFrame:
    if evidence_df.empty:
        return pd.DataFrame(
            columns=[
                "gene_a",
                "gene_b",
                "occurrence_count",
                "support_runs_count",
                "mean_observed_score",
                "max_observed_score",
                "module_support_count",
                "custom_score",
                "in_string",
                "string_score",
                "novelty_status",
                "status",
            ]
        )
    grouped = (
        evidence_df.groupby(["gene_a", "gene_b"], as_index=False)
        .agg(
            occurrence_count=("gene_a", "size"),
            support_runs_count=("source_run", "nunique"),
            mean_observed_score=("observed_score", "mean"),
            max_observed_score=("observed_score", "max"),
        )
        .copy()
    )
    module_counts = (
        evidence_df[evidence_df["evidence_type"] == "module_copresence"]
        .groupby(["gene_a", "gene_b"], as_index=False)
        .size()
        .rename(columns={"size": "module_support_count"})
    )
    grouped = grouped.merge(module_counts, on=["gene_a", "gene_b"], how="left")
    grouped["module_support_count"] = grouped["module_support_count"].fillna(0).astype(int)

    max_runs = max(1, int(grouped["support_runs_count"].max()))
    max_module_support = max(1, int(grouped["module_support_count"].max()))
    recurrence_score = grouped["support_runs_count"].astype(float) / float(max_runs)
    score_component = grouped["mean_observed_score"].astype(float).clip(lower=0.0, upper=1000.0) / 1000.0
    module_component = grouped["module_support_count"].astype(float) / float(max_module_support)
    grouped["custom_score"] = (
        0.5 * recurrence_score + 0.3 * score_component + 0.2 * module_component
    ).clip(lower=0.0, upper=1.0)

    string_map: Dict[Tuple[str, str], float] = {}
    if not string_edges_df.empty:
        for row in string_edges_df.itertuples(index=False):
            a, b = _canonical_edge(str(getattr(row, "source", "")), str(getattr(row, "target", "")))
            score = float(pd.to_numeric(getattr(row, "score", 0.0), errors="coerce") or 0.0)
            string_map[(a, b)] = max(score, string_map.get((a, b), 0.0))
    string_scores: List[float] = []
    in_string_vals: List[bool] = []
    novelty_status: List[str] = []
    status_vals: List[str] = []
    for row in grouped.itertuples(index=False):
        key = (str(row.gene_a), str(row.gene_b))
        score = float(string_map.get(key, 0.0))
        in_string = key in string_map
        string_scores.append(score)
        in_string_vals.append(in_string)
        if not in_string:
            nov = "missing_in_string"
        elif score < float(refinement_score_threshold):
            nov = "known_below_threshold"
        else:
            nov = "known_in_string"
        novelty_status.append(nov)
        custom_score = float(getattr(row, "custom_score"))
        if nov == "missing_in_string" and custom_score >= 0.55:
            status = "accepted_novel"
        elif custom_score >= 0.70:
            status = "accepted_supported"
        else:
            status = "candidate"
        status_vals.append(status)
    grouped["in_string"] = in_string_vals
    grouped["string_score"] = string_scores
    grouped["novelty_status"] = novelty_status
    grouped["status"] = status_vals
    grouped.sort_values(["custom_score", "support_runs_count", "mean_observed_score"], ascending=[False, False, False], inplace=True)
    grouped.reset_index(drop=True, inplace=True)
    return grouped


def init_discovery_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        schema_path = Path(__file__).resolve().parent / "sql" / "network_discovery_schema.sql"
        schema_sql = schema_path.read_text(encoding="utf-8")
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()


def write_snapshot_to_db(
    db_path: Path,
    *,
    snapshot_id: str,
    project_name: Optional[str],
    scan_roots: Sequence[Path],
    config: Dict[str, object],
    edges_df: pd.DataFrame,
    evidence_df: pd.DataFrame,
    module_membership_df: pd.DataFrame,
) -> None:
    import json

    init_discovery_db(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(
            """
            INSERT INTO network_snapshot (
              snapshot_id, created_at, project_name, scan_roots_json,
              n_edges, n_module_memberships, config_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                datetime.now(timezone.utc).isoformat(),
                project_name,
                json.dumps([str(p) for p in scan_roots]),
                int(len(edges_df)),
                int(len(module_membership_df)),
                json.dumps(config, sort_keys=True),
            ),
        )
        if not edges_df.empty:
            edge_rows = [
                (
                    snapshot_id,
                    str(r.gene_a),
                    str(r.gene_b),
                    int(r.occurrence_count),
                    int(r.support_runs_count),
                    float(r.mean_observed_score),
                    float(r.max_observed_score),
                    int(r.module_support_count),
                    float(r.custom_score),
                    1 if bool(r.in_string) else 0,
                    float(r.string_score),
                    str(r.novelty_status),
                    str(r.status),
                )
                for r in edges_df.itertuples(index=False)
            ]
            conn.executemany(
                """
                INSERT INTO network_edge (
                  snapshot_id, gene_a, gene_b, occurrence_count, support_runs_count,
                  mean_observed_score, max_observed_score, module_support_count, custom_score,
                  in_string, string_score, novelty_status, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                edge_rows,
            )
        if not evidence_df.empty:
            evidence_rows = [
                (
                    snapshot_id,
                    str(r.gene_a),
                    str(r.gene_b),
                    str(r.evidence_type),
                    float(pd.to_numeric(getattr(r, "observed_score", 0.0), errors="coerce") or 0.0),
                    str(r.source_run),
                    str(r.source_file),
                )
                for r in evidence_df.itertuples(index=False)
            ]
            conn.executemany(
                """
                INSERT INTO edge_evidence (
                  snapshot_id, gene_a, gene_b, evidence_type, evidence_value, source_run, source_file
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                evidence_rows,
            )
        if not module_membership_df.empty:
            membership_rows = [
                (
                    snapshot_id,
                    str(r.module_label),
                    str(r.gene),
                    str(r.source_run),
                    str(r.source_file),
                )
                for r in module_membership_df.itertuples(index=False)
            ]
            conn.executemany(
                """
                INSERT INTO module_membership (
                  snapshot_id, module_label, gene, source_run, source_file
                ) VALUES (?, ?, ?, ?, ?)
                """,
                membership_rows,
            )
        conn.commit()
    finally:
        conn.close()


def export_curated_edges(
    db_path: Path,
    *,
    snapshot_id: str,
    output_csv: Path,
    statuses: Sequence[str] = ("accepted_novel", "accepted_supported"),
    min_custom_score: float = 0.0,
) -> pd.DataFrame:
    conn = sqlite3.connect(str(db_path))
    try:
        placeholders = ",".join(["?"] * len(statuses))
        query = f"""
            SELECT gene_a AS source, gene_b AS target, custom_score, status
            FROM network_edge
            WHERE snapshot_id = ?
              AND status IN ({placeholders})
              AND custom_score >= ?
            ORDER BY custom_score DESC, source ASC, target ASC
        """
        params: List[object] = [snapshot_id]
        params.extend([str(s) for s in statuses])
        params.append(float(min_custom_score))
        out = pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()
    export_df = out.rename(columns={"custom_score": "score"})[["source", "target", "score"]]
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    export_df.to_csv(output_csv, index=False)
    return export_df


def run_network_discovery(
    *,
    scan_roots: Sequence[Path],
    methyl_enricher_home: str | Path,
    project_name: Optional[str] = None,
    db_path: Optional[str | Path] = None,
    export_path: Optional[str | Path] = None,
    string_species: int = 9606,
    string_required_score_for_novelty: float = 0.0,
    refinement_score_threshold: float = 400.0,
    string_cache_path: Optional[str] = None,
    min_export_score: float = 0.0,
) -> DiscoveryResult:
    resolved_roots = [Path(p).expanduser().resolve() for p in scan_roots]
    paths = resolve_discovery_paths(
        methyl_enricher_home=methyl_enricher_home,
        db_path=db_path,
        export_path=export_path,
    )
    evidence_df, module_membership_df = _collect_edge_artifacts(resolved_roots)
    genes: Set[str] = set()
    if not evidence_df.empty:
        genes.update(str(x) for x in evidence_df["gene_a"].astype(str).tolist())
        genes.update(str(x) for x in evidence_df["gene_b"].astype(str).tolist())
    if not module_membership_df.empty and "gene" in module_membership_df.columns:
        genes.update(str(x) for x in module_membership_df["gene"].astype(str).tolist())
    string_edges_df = fetch_string_edges(
        genes=sorted(genes),
        species=int(string_species),
        required_score=float(string_required_score_for_novelty),
        cache_path=string_cache_path,
    )
    edges_df = _build_edge_table(
        evidence_df,
        string_edges_df=string_edges_df,
        refinement_score_threshold=float(refinement_score_threshold),
    )
    snapshot_id = str(uuid.uuid4())
    config_payload = {
        "string_species": int(string_species),
        "string_required_score_for_novelty": float(string_required_score_for_novelty),
        "refinement_score_threshold": float(refinement_score_threshold),
        "string_cache_path": str(string_cache_path) if string_cache_path else None,
        "min_export_score": float(min_export_score),
    }
    write_snapshot_to_db(
        db_path=paths.db_path,
        snapshot_id=snapshot_id,
        project_name=project_name,
        scan_roots=resolved_roots,
        config=config_payload,
        edges_df=edges_df,
        evidence_df=evidence_df,
        module_membership_df=module_membership_df,
    )
    export_curated_edges(
        db_path=paths.db_path,
        snapshot_id=snapshot_id,
        output_csv=paths.export_path,
        min_custom_score=float(min_export_score),
    )
    return DiscoveryResult(
        snapshot_id=snapshot_id,
        edges_df=edges_df,
        module_membership_df=module_membership_df,
        db_path=paths.db_path,
        export_path=paths.export_path,
    )

