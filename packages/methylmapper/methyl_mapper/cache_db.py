"""
SQLite cache store for gene-disease enrichment.

This replaces JSON cache persistence for shared multi-VM usage.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


class SQLiteCacheStore:
    """Thread-safe SQLite WAL cache with simple upsert/query methods."""

    SCHEMA_VERSION = 1

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS associations (
                    source TEXT NOT NULL,
                    gene TEXT NOT NULL,
                    disease_term TEXT NOT NULL,
                    ts REAL NOT NULL,
                    value_json TEXT NOT NULL,
                    PRIMARY KEY (source, gene, disease_term)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS disease_ids (
                    disease_term TEXT PRIMARY KEY,
                    ts REAL NOT NULL,
                    disease_id TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS target_ids (
                    gene_name TEXT PRIMARY KEY,
                    ts REAL NOT NULL,
                    target_id TEXT
                )
                """
            )
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(self.SCHEMA_VERSION),),
            )
            conn.execute("COMMIT")

    def get_metadata(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row is not None else None

    def set_metadata(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)",
                (str(key), str(value)),
            )
            conn.execute("COMMIT")

    def upsert_association(self, source: str, gene: str, disease_term: str, ts: float, value: Dict) -> None:
        payload = json.dumps(value or {})
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO associations(source, gene, disease_term, ts, value_json)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(source, gene, disease_term)
                DO UPDATE SET ts=excluded.ts, value_json=excluded.value_json
                """,
                (str(source), str(gene).upper(), str(disease_term), float(ts), payload),
            )
            conn.execute("COMMIT")

    def fetch_associations_batch(
        self,
        source: str,
        genes: Sequence[str],
        disease_term: str,
    ) -> Dict[str, Dict[str, object]]:
        norm_genes = [str(g).strip().upper() for g in genes if str(g).strip()]
        if not norm_genes:
            return {}
        placeholders = ",".join("?" for _ in norm_genes)
        sql = (
            "SELECT gene, ts, value_json FROM associations "
            "WHERE source = ? AND disease_term = ? AND gene IN (" + placeholders + ")"
        )
        out: Dict[str, Dict[str, object]] = {}
        with self._connect() as conn:
            rows = conn.execute(sql, (str(source), str(disease_term), *norm_genes)).fetchall()
            for row in rows:
                try:
                    parsed = json.loads(row["value_json"] or "{}")
                except json.JSONDecodeError:
                    parsed = {}
                out[str(row["gene"]).upper()] = {
                    "ts": float(row["ts"]) if row["ts"] is not None else None,
                    "value": parsed if isinstance(parsed, dict) else {},
                }
        return out

    def upsert_disease_id(self, disease_term: str, ts: float, disease_id: Optional[str]) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO disease_ids(disease_term, ts, disease_id)
                VALUES(?, ?, ?)
                ON CONFLICT(disease_term)
                DO UPDATE SET ts=excluded.ts, disease_id=excluded.disease_id
                """,
                (str(disease_term), float(ts), disease_id),
            )
            conn.execute("COMMIT")

    def get_disease_id(self, disease_term: str) -> Optional[Tuple[float, Optional[str]]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ts, disease_id FROM disease_ids WHERE disease_term = ?",
                (str(disease_term),),
            ).fetchone()
            if row is None:
                return None
            return float(row["ts"]) if row["ts"] is not None else time.time(), row["disease_id"]

    def delete_disease_id(self, disease_term: str) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM disease_ids WHERE disease_term = ?", (str(disease_term),))
            conn.execute("COMMIT")

    def upsert_target_id(self, gene_name: str, ts: float, target_id: Optional[str]) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO target_ids(gene_name, ts, target_id)
                VALUES(?, ?, ?)
                ON CONFLICT(gene_name)
                DO UPDATE SET ts=excluded.ts, target_id=excluded.target_id
                """,
                (str(gene_name).upper(), float(ts), target_id),
            )
            conn.execute("COMMIT")

    def get_target_id(self, gene_name: str) -> Optional[Tuple[float, Optional[str]]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ts, target_id FROM target_ids WHERE gene_name = ?",
                (str(gene_name).upper(),),
            ).fetchone()
            if row is None:
                return None
            return float(row["ts"]) if row["ts"] is not None else time.time(), row["target_id"]

    def delete_target_id(self, gene_name: str) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM target_ids WHERE gene_name = ?", (str(gene_name).upper(),))
            conn.execute("COMMIT")

    def import_legacy_payload(self, payload: Dict) -> None:
        associations = payload.get("associations") or []
        disease_ids = payload.get("disease_ids") or []
        target_ids = payload.get("target_ids") or []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if isinstance(associations, list):
                for row in associations:
                    conn.execute(
                        """
                        INSERT INTO associations(source, gene, disease_term, ts, value_json)
                        VALUES(?, ?, ?, ?, ?)
                        ON CONFLICT(source, gene, disease_term)
                        DO UPDATE SET ts=excluded.ts, value_json=excluded.value_json
                        """,
                        (
                            str(row.get("source", "")).strip(),
                            str(row.get("gene", "")).strip().upper(),
                            str(row.get("disease_term", "")).strip(),
                            float(row.get("ts")) if row.get("ts") is not None else time.time(),
                            str(row.get("value_json", "{}")),
                        ),
                    )
            if isinstance(disease_ids, list):
                for row in disease_ids:
                    conn.execute(
                        """
                        INSERT INTO disease_ids(disease_term, ts, disease_id)
                        VALUES(?, ?, ?)
                        ON CONFLICT(disease_term)
                        DO UPDATE SET ts=excluded.ts, disease_id=excluded.disease_id
                        """,
                        (
                            str(row.get("disease_term", "")).strip(),
                            float(row.get("ts")) if row.get("ts") is not None else time.time(),
                            row.get("disease_id"),
                        ),
                    )
            if isinstance(target_ids, list):
                for row in target_ids:
                    conn.execute(
                        """
                        INSERT INTO target_ids(gene_name, ts, target_id)
                        VALUES(?, ?, ?)
                        ON CONFLICT(gene_name)
                        DO UPDATE SET ts=excluded.ts, target_id=excluded.target_id
                        """,
                        (
                            str(row.get("gene_name", "")).strip().upper(),
                            float(row.get("ts")) if row.get("ts") is not None else time.time(),
                            row.get("target_id"),
                        ),
                    )
            conn.execute("COMMIT")

    def association_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM associations").fetchone()
            return int(row["n"]) if row is not None else 0

    def prune_expired(
        self,
        source_ttls_days: Dict[str, Optional[int]],
        disease_ttl_days: Optional[int],
        target_ttl_days: Optional[int],
    ) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT source, gene, disease_term, ts FROM associations").fetchall()
            for row in rows:
                ttl = source_ttls_days.get(str(row["source"]))
                if ttl == 0:
                    conn.execute(
                        "DELETE FROM associations WHERE source = ? AND gene = ? AND disease_term = ?",
                        (row["source"], row["gene"], row["disease_term"]),
                    )
                elif ttl is not None and (now - float(row["ts"])) > (float(ttl) * 86400.0):
                    conn.execute(
                        "DELETE FROM associations WHERE source = ? AND gene = ? AND disease_term = ?",
                        (row["source"], row["gene"], row["disease_term"]),
                    )
            if disease_ttl_days == 0:
                conn.execute("DELETE FROM disease_ids")
            elif disease_ttl_days is not None:
                threshold = now - (float(disease_ttl_days) * 86400.0)
                conn.execute("DELETE FROM disease_ids WHERE ts < ?", (threshold,))
            if target_ttl_days == 0:
                conn.execute("DELETE FROM target_ids")
            elif target_ttl_days is not None:
                threshold = now - (float(target_ttl_days) * 86400.0)
                conn.execute("DELETE FROM target_ids WHERE ts < ?", (threshold,))
            conn.execute("COMMIT")

