from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from methyl_mapper.cache_db import SQLiteCacheStore
from methyl_mapper.gene_disease_enricher import GeneDiseaseEnricher


def test_sqlite_cache_store_wal_and_upsert_batch(tmp_path: Path):
    db_path = tmp_path / "cache" / "gene_disease_cache.sqlite"
    store = SQLiteCacheStore(db_path)
    assert db_path.is_file()

    with sqlite3.connect(str(db_path)) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"

    store.upsert_association("grok", "TP53", "prostate cancer", 100.0, {"associated": True})
    store.upsert_association("open_targets", "TP53", "prostate cancer", 101.0, {"associated": False})
    rows = store.fetch_associations_batch("grok", ["TP53", "BRCA1"], "prostate cancer")
    assert "TP53" in rows
    assert rows["TP53"]["value"]["associated"] is True


def test_sqlite_cache_store_ttl_prune(tmp_path: Path):
    db_path = tmp_path / "cache.sqlite"
    store = SQLiteCacheStore(db_path)
    store.upsert_association("grok", "TP53", "disease", time.time() - 10 * 86400, {"associated": True})
    store.upsert_association("open_targets", "TP53", "disease", time.time(), {"associated": True})
    store.prune_expired(
        source_ttls_days={"grok": 1, "open_targets": 30},
        disease_ttl_days=7,
        target_ttl_days=7,
    )
    assert "TP53" not in store.fetch_associations_batch("grok", ["TP53"], "disease")
    assert "TP53" in store.fetch_associations_batch("open_targets", ["TP53"], "disease")


def test_sqlite_cache_store_concurrency_smoke(tmp_path: Path):
    db_path = tmp_path / "cache.sqlite"
    store = SQLiteCacheStore(db_path)

    def _writer(offset: int):
        for i in range(40):
            store.upsert_association(
                "grok",
                f"GENE_{offset}_{i}",
                "disease",
                time.time(),
                {"associated": True, "i": i},
            )

    threads = [threading.Thread(target=_writer, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert store.association_count() >= 160


def test_enricher_json_migration_once(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    legacy = cache_dir / "gene_disease_cache.json"
    legacy_payload = {
        "version": 3,
        "associations": [
            {
                "source": "grok",
                "gene": "TP53",
                "disease_term": "prostate cancer",
                "ts": time.time(),
                "value_json": json.dumps({"associated": True}),
            }
        ],
        "disease_ids": [],
        "target_ids": [],
    }
    legacy.write_text(json.dumps(legacy_payload), encoding="utf-8")

    enricher = GeneDiseaseEnricher(
        use_grok=False,
        use_open_targets=False,
        use_disgenet=False,
        cache_enabled=True,
        cache_dir=cache_dir,
        cache_backend="sqlite",
    )
    assert enricher.cache_store is not None
    assert enricher.cache_store.association_count() == 1
    assert (cache_dir / "gene_disease_cache.json.migrated").exists()
    assert enricher.cache_store.get_metadata("legacy_json_migrated") == "1"

    # Idempotent: second init should not duplicate rows.
    enricher2 = GeneDiseaseEnricher(
        use_grok=False,
        use_open_targets=False,
        use_disgenet=False,
        cache_enabled=True,
        cache_dir=cache_dir,
        cache_backend="sqlite",
    )
    assert enricher2.cache_store is not None
    assert enricher2.cache_store.association_count() == 1


def test_enricher_json_migration_reimports_new_json_after_marker(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    legacy = cache_dir / "gene_disease_cache.json"
    legacy.write_text(
        json.dumps(
            {
                "version": 3,
                "associations": [
                    {
                        "source": "grok",
                        "gene": "TP53",
                        "disease_term": "prostate cancer",
                        "ts": time.time(),
                        "value_json": json.dumps({"associated": True}),
                    }
                ],
                "disease_ids": [],
                "target_ids": [],
            }
        ),
        encoding="utf-8",
    )
    enricher = GeneDiseaseEnricher(
        use_grok=False,
        use_open_targets=False,
        use_disgenet=False,
        cache_enabled=True,
        cache_dir=cache_dir,
        cache_backend="sqlite",
    )
    assert enricher.cache_store is not None
    assert enricher.cache_store.association_count() == 1
    assert enricher.cache_store.get_metadata("legacy_json_migrated") == "1"

    # Simulate a new legacy JSON appearing later (e.g. from older tool version).
    legacy.write_text(
        json.dumps(
            {
                "version": 3,
                "associations": [
                    {
                        "source": "grok",
                        "gene": "TP53",
                        "disease_term": "prostate cancer",
                        "ts": time.time(),
                        "value_json": json.dumps({"associated": True}),
                    },
                    {
                        "source": "grok",
                        "gene": "MYC",
                        "disease_term": "prostate cancer",
                        "ts": time.time(),
                        "value_json": json.dumps({"associated": False}),
                    },
                ],
                "disease_ids": [],
                "target_ids": [],
            }
        ),
        encoding="utf-8",
    )

    enricher2 = GeneDiseaseEnricher(
        use_grok=False,
        use_open_targets=False,
        use_disgenet=False,
        cache_enabled=True,
        cache_dir=cache_dir,
        cache_backend="sqlite",
    )
    assert enricher2.cache_store is not None
    assert enricher2.cache_store.association_count() >= 2
    assert (cache_dir / "gene_disease_cache.json").exists() is False


def test_enricher_migrates_grok_only_json_and_keeps_open_targets_miss(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    legacy = cache_dir / "gene_disease_cache.json"
    legacy_payload = {
        "version": 3,
        "associations": [
            {
                "source": "grok",
                "gene": "MYC",
                "disease_term": "prostate cancer",
                "ts": time.time(),
                "value_json": json.dumps({"associated": True, "association_type": "indirect"}),
            }
        ],
        "disease_ids": [],
        "target_ids": [],
    }
    legacy.write_text(json.dumps(legacy_payload), encoding="utf-8")

    enricher = GeneDiseaseEnricher(
        use_grok=False,
        use_open_targets=False,
        use_disgenet=False,
        cache_enabled=True,
        cache_dir=cache_dir,
        cache_backend="sqlite",
    )
    grok_rows = enricher._cache_get_batch("grok", ["MYC"], "prostate cancer")
    ot_rows = enricher._cache_get_batch("open_targets", ["MYC"], "prostate cancer")
    assert "MYC" in grok_rows
    assert "MYC" not in ot_rows


def test_enricher_partial_source_cache_behavior(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    enricher = GeneDiseaseEnricher(
        use_grok=False,
        use_open_targets=False,
        use_disgenet=False,
        cache_enabled=True,
        cache_dir=cache_dir,
        cache_backend="sqlite",
    )
    key = enricher._cache_key("grok", "TP53", "prostate cancer")
    enricher._cache_set(key, {"associated": True, "association_type": "direct"})

    from_grok = enricher._cache_get_batch("grok", ["TP53"], "prostate cancer")
    from_ot = enricher._cache_get_batch("open_targets", ["TP53"], "prostate cancer")
    assert "TP53" in from_grok
    assert "TP53" not in from_ot


def test_empty_cached_payload_is_treated_as_miss(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    enricher = GeneDiseaseEnricher(
        use_grok=False,
        use_open_targets=False,
        use_disgenet=False,
        cache_enabled=True,
        cache_dir=cache_dir,
        cache_backend="sqlite",
    )
    assert enricher.cache_store is not None
    # Simulate bad migrated Open Targets row with only empty defaults.
    enricher.cache_store.upsert_association(
        source="open_targets",
        gene="TP53",
        disease_term="prostate cancer",
        ts=time.time(),
        value={
            "associated": False,
            "association_type": "none",
            "evidence_level": "none",
            "description": None,
            "publications": 0,
            "functional_role": None,
            "score": 0.0,
            "source": "open_targets",
        },
    )
    rows = enricher._cache_get_batch("open_targets", ["TP53"], "prostate cancer")
    assert "TP53" not in rows


def test_grok_explicit_negative_payload_is_cache_hit(tmp_path: Path):
    cache_dir = tmp_path / "cache"
    enricher = GeneDiseaseEnricher(
        use_grok=False,
        use_open_targets=False,
        use_disgenet=False,
        cache_enabled=True,
        cache_dir=cache_dir,
        cache_backend="sqlite",
    )
    assert enricher.cache_store is not None
    enricher.cache_store.upsert_association(
        source="grok",
        gene="TP53",
        disease_term="prostate cancer",
        ts=time.time(),
        value={
            "associated": False,
            "association_type": "none",
            "evidence_level": "none",
            "description": None,
            "publications": 0,
            "functional_role": None,
            "source": "grok",
        },
    )
    rows = enricher._cache_get_batch("grok", ["TP53"], "prostate cancer")
    assert "TP53" in rows
    assert rows["TP53"]["associated"] is False

