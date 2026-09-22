"""
Gene-disease association enrichment using Grok API and biomedical databases.

This module enriches gene mapping results with disease associations, particularly
focused on cancer types like early-stage prostate cancer.
"""

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock, local
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse, urlunparse
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .cache_db import SQLiteCacheStore
from .secure_credentials import SecureCredentialManager

logger = logging.getLogger(__name__)

# Cache format version (3 = dict-backed runtime index + persisted records)
CACHE_VERSION = 3
CACHE_COLUMNS = ["source", "gene", "disease_term", "ts", "value_json"]
DISEASE_CACHE_COLUMNS = ["disease_term", "ts", "disease_id"]
TARGET_CACHE_COLUMNS = ["gene_name", "ts", "target_id"]

DEFAULT_CACHE_TTL_DAYS = 30
DEFAULT_GROK_CACHE_TTL_DAYS = 30
DEFAULT_GROK_BATCH_SIZE = 16
DEFAULT_GROK_MAX_WORKERS = 1
DEFAULT_GROK_USE_XAI_BATCH_API = False
DEFAULT_GROK_BATCH_POLL_INTERVAL = 2.0
DEFAULT_GROK_BATCH_SUBMIT_CHUNK_SIZE = 200
GROK_COMPLETION_MODEL = "grok-4-latest"
# xAI Batch limits include 1 batch create per second per team; HTTP 429 can still occur — retry generously.
XAI_BATCH_HTTP_MIN_ATTEMPTS = 12
XAI_BATCH_CREATE_MIN_INTERVAL_SEC = 1.15
DEFAULT_OPEN_TARGETS_MAX_WORKERS = 8
DEFAULT_DISGENET_MAX_WORKERS = 8
DEFAULT_SOURCE_MAX_WORKERS = 3
NULL_CACHE_VALUE = "__NULL__"

# Evidence level ordering for thresholding
EVIDENCE_LEVEL_ORDER = {
    "none": 0,
    "low": 1,
    "medium": 2,
    "high": 3
}

# Enrichment profiles for quick testing
ENRICHMENT_PROFILES = {
    "strict": {
        "min_evidence_level": "high",
        "min_publications": 2,
        "min_disgenet_score": 0.5,
        "min_open_targets_score": 0.3,
        "allow_predicted": False
    },
    "balanced": {
        "min_evidence_level": "medium",
        "min_publications": 0,
        "min_disgenet_score": 0.3,
        "min_open_targets_score": 0.1,
        "allow_predicted": False
    },
    "permissive": {
        "min_evidence_level": "low",
        "min_publications": 0,
        "min_disgenet_score": 0.0,
        "min_open_targets_score": 0.0,
        "allow_predicted": True
    }
}


def _apply_enrichment_profile(
    enrichment_profile: Optional[str],
    min_evidence_level: Optional[str],
    min_publications: Optional[int],
    min_disgenet_score: Optional[float],
    min_open_targets_score: Optional[float],
    allow_predicted: Optional[bool]
) -> Dict[str, Union[str, int, float, bool]]:
    """Apply profile defaults for enrichment thresholds."""
    if enrichment_profile:
        profile_key = enrichment_profile.lower()
        if profile_key not in ENRICHMENT_PROFILES:
            raise ValueError(
                f"Unknown enrichment_profile '{enrichment_profile}'. "
                f"Valid options: {list(ENRICHMENT_PROFILES.keys())}"
            )
        profile = ENRICHMENT_PROFILES[profile_key]
        if min_evidence_level is None:
            min_evidence_level = profile["min_evidence_level"]
        if min_publications is None:
            min_publications = profile["min_publications"]
        if min_disgenet_score is None:
            min_disgenet_score = profile["min_disgenet_score"]
        if min_open_targets_score is None:
            min_open_targets_score = profile["min_open_targets_score"]
        if allow_predicted is None:
            allow_predicted = profile["allow_predicted"]

    # Fallback defaults
    if min_evidence_level is None:
        min_evidence_level = "medium"
    if min_publications is None:
        min_publications = 0
    if min_disgenet_score is None:
        min_disgenet_score = 0.0
    if min_open_targets_score is None:
        min_open_targets_score = 0.0
    if allow_predicted is None:
        allow_predicted = False

    return {
        "min_evidence_level": min_evidence_level,
        "min_publications": min_publications,
        "min_disgenet_score": min_disgenet_score,
        "min_open_targets_score": min_open_targets_score,
        "allow_predicted": allow_predicted
    }


class ProgressIndicator:
    """
    Simple progress indicator for API queries.
    """

    def __init__(self, total: int, description: str = "Processing", update_interval: int = 10):
        self.total = total
        self.current = 0
        self.description = description
        self.update_interval = update_interval
        self.start_time = time.time()
        self.success_count = 0
        self.error_count = 0
        self.last_update = 0

    def update(self, success: bool = True):
        """Update progress counter."""
        self.current += 1
        if success:
            self.success_count += 1
        else:
            self.error_count += 1

        # Only print progress updates at intervals to avoid spam
        if self.current % self.update_interval == 0 or self.current == self.total:
            self._print_progress()

    def _print_progress(self):
        """Print current progress."""
        elapsed = time.time() - self.start_time
        rate = self.current / elapsed if elapsed > 0 else 0

        if self.current < self.total:
            eta = (self.total - self.current) / rate if rate > 0 else 0
            eta_str = f"{eta:.0f}s" if eta < 60 else f"{eta/60:.1f}m"
        else:
            eta_str = "complete"

        percent = (self.current / self.total) * 100
        status = f"{self.success_count}✓/{self.error_count}✗" if self.error_count > 0 else f"{self.success_count}✓"

        print(f"\r{self.description}: {self.current}/{self.total} ({percent:.1f}%) | {status} | ETA: {eta_str}", end="", flush=True)

        if self.current == self.total:
            print()  # New line at completion


class GeneDiseaseEnricher:
    """
    Enrich genes with disease associations using Grok API and biomedical databases.
    
    Supports:
    - Grok API queries for gene-disease associations
    - Open Targets database (default)
    - DisGeNET database (optional)
    - NCBI Gene database (fallback)
    """

    _xai_batch_create_lock = Lock()
    _xai_last_batch_create_at = 0.0

    def __init__(
        self,
        grok_api_key: Optional[str] = None,
        disgenet_api_key: Optional[str] = None,
        grok_api_url: str = "https://api.x.ai/v1/chat/completions",
        open_targets_api_url: str = "https://api.platform.opentargets.org/api/v4/graphql",
        disease_term: str = "early-stage prostate cancer",
        use_grok: bool = True,
        use_disgenet: bool = False,
        use_open_targets: bool = True,
        enrichment_profile: Optional[str] = None,
        min_evidence_level: Optional[str] = None,
        min_publications: Optional[int] = None,
        min_disgenet_score: Optional[float] = None,
        min_open_targets_score: Optional[float] = None,
        allow_predicted: Optional[bool] = None,
        cache_enabled: bool = True,
        cache_dir: Optional[Path] = None,
        cache_backend: str = "sqlite",
        cache_db_path: Optional[Path] = None,
        cache_ttl_days: Optional[int] = DEFAULT_CACHE_TTL_DAYS,
        grok_cache_ttl_days: Optional[int] = DEFAULT_GROK_CACHE_TTL_DAYS,
        rate_limit_delay: float = 5.0,
        max_retries: int = 6,
        grok_429_inter_batch_sleep: float = 180.0,
        source_max_workers: int = DEFAULT_SOURCE_MAX_WORKERS,
        grok_batch_size: int = DEFAULT_GROK_BATCH_SIZE,
        grok_max_workers: int = DEFAULT_GROK_MAX_WORKERS,
        grok_use_xai_batch_api: bool = DEFAULT_GROK_USE_XAI_BATCH_API,
        grok_batch_poll_interval: float = DEFAULT_GROK_BATCH_POLL_INTERVAL,
        grok_batch_submit_chunk_size: int = DEFAULT_GROK_BATCH_SUBMIT_CHUNK_SIZE,
        open_targets_max_workers: int = DEFAULT_OPEN_TARGETS_MAX_WORKERS,
        disgenet_max_workers: int = DEFAULT_DISGENET_MAX_WORKERS,
        azure_key_vault_url: Optional[str] = None,
        azure_secret_name: Optional[str] = None,
        encrypted_file_path: Optional[Path] = None,
        methyl_mapper_home: Optional[Path] = None,
    ):
        """
        Initialize GeneDiseaseEnricher.

        Args:
            grok_api_key: Grok API key (optional, will use secure storage if not provided)
            disgenet_api_key: DisGeNET API key (optional, will use secure storage if not provided)
            grok_api_url: Grok API endpoint URL
            open_targets_api_url: Open Targets GraphQL endpoint URL
            disease_term: Disease term to search for (e.g., "early-stage prostate cancer")
            use_grok: Whether to use Grok API for enrichment
            use_disgenet: Whether to use DisGeNET database for enrichment
            use_open_targets: Whether to use Open Targets database for enrichment
            enrichment_profile: Preset threshold profile (strict, balanced, permissive)
            min_evidence_level: Minimum evidence level to count as disease-associated
                                (none, low, medium, high)
            min_publications: Minimum number of publications required
            min_disgenet_score: Minimum DisGeNET score required (0.0-1.0)
            min_open_targets_score: Minimum Open Targets score required (0.0-1.0)
            allow_predicted: Whether to allow "predicted" associations
            cache_enabled: Whether to persist cache to disk
            cache_dir: Directory for disk cache (default: ~/.methyl_mapper/cache)
            cache_backend: Cache persistence backend (sqlite)
            cache_db_path: Explicit cache DB path (default: <cache_dir>/gene_disease_cache.sqlite)
            cache_ttl_days: Disk cache TTL in days for Open Targets / DisGeNET and metadata caches
            grok_cache_ttl_days: Disk cache TTL in days for Grok results (default: 7; set 0 to refresh each run)
            rate_limit_delay: Minimum delay between starting consecutive Grok batch requests (seconds)
            max_retries: Maximum retry attempts per Grok batch (429 uses long backoff)
            grok_429_inter_batch_sleep: Extra sleep after a batch exhausts retries (often 429) before the next batch
            source_max_workers: Max workers when querying multiple sources in parallel
            grok_batch_size: Number of genes per Grok batch request
            grok_max_workers: Max concurrent Grok batch requests (realtime API only; ignored for xAI Batch API)
            grok_use_xai_batch_api: If True (default), use xAI Batch API (async queue, avoids realtime rate limits)
            grok_batch_poll_interval: Seconds between GET /batches/{id} polls while waiting for xAI batch
            grok_batch_submit_chunk_size: Max requests per POST when adding to an xAI batch
            open_targets_max_workers: Max concurrent Open Targets gene requests
            disgenet_max_workers: Max concurrent DisGeNET gene requests
            azure_key_vault_url: Azure Key Vault URL (or set AZURE_KEY_VAULT_URL env var)
            azure_secret_name: Optional Key Vault secret name override (defaults: grok-api-key / disgenet-api-key)
            encrypted_file_path: Path to encrypted credential file (optional)
            methyl_mapper_home: Root for cache/ and credentials/ when defaults are used (default: ~/.methyl_mapper)
        """
        _mm_home = Path(methyl_mapper_home).expanduser() if methyl_mapper_home else None
        # Initialize secure credential managers
        self.grok_credential_manager = SecureCredentialManager(
            credential_name="grok_api_key",
            azure_key_vault_url=azure_key_vault_url,
            azure_secret_name=azure_secret_name,
            encrypted_file_path=encrypted_file_path,
            env_var_name="GROK_API_KEY",
            methyl_mapper_home=_mm_home,
        ) if use_grok else None

        self.disgenet_credential_manager = SecureCredentialManager(
            credential_name="disgenet_api_key",
            azure_key_vault_url=azure_key_vault_url,
            azure_secret_name=azure_secret_name,
            encrypted_file_path=encrypted_file_path,
            env_var_name="DISGENET_API_KEY",
            methyl_mapper_home=_mm_home,
        ) if use_disgenet else None

        # Get credentials using secure managers
        self.grok_api_key = self.grok_credential_manager.get_credential(explicit_key=grok_api_key) if self.grok_credential_manager else None
        self.disgenet_api_key = self.disgenet_credential_manager.get_credential(explicit_key=disgenet_api_key) if self.disgenet_credential_manager else None
        thresholds = _apply_enrichment_profile(
            enrichment_profile=enrichment_profile,
            min_evidence_level=min_evidence_level,
            min_publications=min_publications,
            min_disgenet_score=min_disgenet_score,
            min_open_targets_score=min_open_targets_score,
            allow_predicted=allow_predicted
        )

        self.grok_api_url = grok_api_url
        self.open_targets_api_url = open_targets_api_url
        self.disease_term = disease_term
        self.use_grok = use_grok
        self.use_disgenet = use_disgenet
        self.use_open_targets = use_open_targets
        self.enrichment_profile = enrichment_profile
        self.min_evidence_level = thresholds["min_evidence_level"].lower()
        self.min_publications = max(0, int(thresholds["min_publications"]))
        self.min_disgenet_score = float(thresholds["min_disgenet_score"])
        self.min_open_targets_score = float(thresholds["min_open_targets_score"])
        self.allow_predicted = bool(thresholds["allow_predicted"])
        self.rate_limit_delay = max(0.0, float(rate_limit_delay))
        self.max_retries = max(1, int(max_retries))
        self.grok_429_inter_batch_sleep = max(0.0, float(grok_429_inter_batch_sleep))
        self.source_max_workers = max(1, int(source_max_workers))
        self.grok_batch_size = min(20, max(1, int(grok_batch_size)))
        self.grok_max_workers = max(1, int(grok_max_workers))
        self.grok_use_xai_batch_api = bool(grok_use_xai_batch_api)
        self.grok_batch_poll_interval = max(0.5, float(grok_batch_poll_interval))
        self.grok_batch_submit_chunk_size = max(1, int(grok_batch_submit_chunk_size))
        self.open_targets_max_workers = max(1, int(open_targets_max_workers))
        self.disgenet_max_workers = max(1, int(disgenet_max_workers))

        self.cache_enabled = cache_enabled
        self.cache_ttl_days = None if cache_ttl_days is None else int(cache_ttl_days)
        self.grok_cache_ttl_days = None if grok_cache_ttl_days is None else int(grok_cache_ttl_days)
        if cache_dir is not None:
            self.cache_dir = Path(cache_dir).expanduser()
        elif _mm_home is not None:
            self.cache_dir = _mm_home / "cache"
        else:
            self.cache_dir = Path.home() / ".methyl_mapper" / "cache"
        self.cache_file = self.cache_dir / "gene_disease_cache.json"
        self.cache_backend = str(cache_backend or "sqlite").strip().lower()
        self.cache_db_path = (
            Path(cache_db_path).expanduser()
            if cache_db_path is not None
            else (self.cache_dir / "gene_disease_cache.sqlite")
        )
        if self.cache_backend != "sqlite":
            raise ValueError(f"Unsupported cache_backend '{self.cache_backend}'. Supported: sqlite")
        self.cache_store: Optional[SQLiteCacheStore] = (
            SQLiteCacheStore(self.cache_db_path) if self.cache_enabled else None
        )

        if self.min_evidence_level not in EVIDENCE_LEVEL_ORDER:
            raise ValueError(
                f"min_evidence_level must be one of {list(EVIDENCE_LEVEL_ORDER.keys())}, "
                f"got '{min_evidence_level}'"
            )
        if self.min_disgenet_score < 0.0 or self.min_disgenet_score > 1.0:
            raise ValueError("min_disgenet_score must be between 0.0 and 1.0")
        if self.min_open_targets_score < 0.0 or self.min_open_targets_score > 1.0:
            raise ValueError("min_open_targets_score must be between 0.0 and 1.0")
        
        self._thread_local = local()
        self._cache_lock = Lock()
        self._grok_request_lock = Lock()
        self._grok_next_start_time = 0.0
        self._association_runtime_cache: Dict[str, Dict] = {}
        self._association_disk_cache: Dict[str, Dict[str, Union[float, Dict]]] = {}
        self._disease_runtime_cache: Dict[str, Optional[str]] = {}
        self._disease_disk_cache: Dict[str, Dict[str, Optional[Union[str, float]]]] = {}
        self._target_runtime_cache: Dict[str, Optional[str]] = {}
        self._target_disk_cache: Dict[str, Dict[str, Optional[Union[str, float]]]] = {}
        self._cache_dirty = False
        self._load_disk_cache()

    def _create_session(self) -> requests.Session:
        """Create a requests session configured for retryable API traffic."""
        session = requests.Session()
        # Do not retry 429 in the session so _query_grok_batch can apply long backoff and respect Retry-After
        retry_strategy = Retry(
            total=self.max_retries,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=None,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _get_session(self) -> requests.Session:
        """Return a per-thread session so concurrent requests do not share mutable state."""
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = self._create_session()
            self._thread_local.session = session
        return session

    def _cache_ttl_for_source(self, source: str) -> Optional[int]:
        """Resolve disk TTL for the given cache source."""
        if source == "grok":
            return self.grok_cache_ttl_days
        return self.cache_ttl_days

    @staticmethod
    def _dedupe_gene_names(gene_names: List[str]) -> List[str]:
        """Normalize and deduplicate gene names while preserving input order."""
        deduped: List[str] = []
        seen = set()
        for gene in gene_names:
            normalized = str(gene).strip()
            if not normalized:
                continue
            gene_upper = normalized.upper()
            if gene_upper in seen:
                continue
            seen.add(gene_upper)
            deduped.append(normalized)
        return deduped

    def _build_enrichment_payload(
        self,
        gene_names: List[str],
        disease_term: Optional[str] = None,
    ) -> Dict[str, Dict]:
        """Fetch enrichment results once for a set of genes and return reusable payload."""
        disease_term = disease_term or self.disease_term
        unique_genes = self._dedupe_gene_names(gene_names)
        if not unique_genes:
            return {
                "genes": [],
                "hyperlinks": {},
                "grok": {},
                "open_targets": {},
                "disgenet": {},
                "merged": {},
            }

        logger.info(f"Enriching {len(unique_genes)} unique genes with disease associations...")
        source_results = self._query_enabled_sources(unique_genes, disease_term)
        hyperlinks = self._generate_gene_hyperlinks(unique_genes)
        merged_results = self._merge_results(
            source_results.get("grok", {}),
            source_results.get("open_targets", {}),
            source_results.get("disgenet", {}),
            unique_genes,
        )
        # Diagnostic: counts per source (raw) and after thresholds
        n_grok_raw = sum(1 for g in unique_genes if source_results.get("grok", {}).get(g.upper(), {}).get("associated"))
        n_ot_raw = sum(1 for g in unique_genes if source_results.get("open_targets", {}).get(g.upper(), {}).get("associated"))
        n_merged_pass = sum(1 for g in unique_genes if self._association_meets_thresholds(merged_results.get(g.upper(), {})))
        logger.info(f"Disease enrichment: Grok raw={n_grok_raw}, Open Targets raw={n_ot_raw}, merged (pass thresholds)={n_merged_pass} genes")
        if n_merged_pass == 0 and (n_grok_raw > 0 or n_ot_raw > 0):
            logger.warning(
                "All genes have disease_associated=False after thresholds. Associations were found (Grok/Open Targets) but filtered out. "
                "Use --enrich-profile permissive or lower min_evidence_level / allow_predicted to include them."
            )
        elif n_merged_pass == 0 and len(unique_genes) > 0:
            logger.warning(
                "No disease associations returned from any source. Check disease_term, Grok API key (GROK_API_KEY), and Open Targets connectivity."
            )
        return {
            "genes": unique_genes,
            "hyperlinks": hyperlinks,
            "grok": source_results.get("grok", {}),
            "open_targets": source_results.get("open_targets", {}),
            "disgenet": source_results.get("disgenet", {}),
            "merged": merged_results,
        }

    def _query_enabled_sources(
        self,
        gene_names: List[str],
        disease_term: str,
    ) -> Dict[str, Dict[str, Dict]]:
        """Run enabled enrichment sources, overlapping network I/O when possible."""
        tasks = []
        if self.use_grok:
            tasks.append(("grok", self.query_grok_api))
        if self.use_open_targets:
            tasks.append(("open_targets", self.query_open_targets))
        if self.use_disgenet:
            tasks.append(("disgenet", self.query_disgenet))

        results: Dict[str, Dict[str, Dict]] = {
            "grok": {},
            "open_targets": {},
            "disgenet": {},
        }
        if not tasks:
            return results

        if len(tasks) == 1:
            name, func = tasks[0]
            results[name] = func(gene_names, disease_term)
            return results

        max_workers = min(self.source_max_workers, len(tasks))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_name = {
                executor.submit(func, gene_names, disease_term): name
                for name, func in tasks
            }
            for future in as_completed(future_to_name):
                name = future_to_name[future]
                try:
                    results[name] = future.result()
                except Exception as exc:
                    logger.warning(f"{name} enrichment failed: {exc}")
                    results[name] = {}

        return results

    def _apply_enrichment_payload(
        self,
        df: pd.DataFrame,
        gene_column: str,
        payload: Dict[str, Dict],
        separate_sources: bool = False,
    ) -> Union[pd.DataFrame, Dict[str, pd.DataFrame]]:
        """Apply a pre-fetched enrichment payload to one or more dataframes."""
        hyperlinks = payload.get("hyperlinks", {})
        sources_enabled = {
            "grok": self.use_grok,
            "open_targets": self.use_open_targets,
            "disgenet": self.use_disgenet,
        }
        sources_enabled = {k: v for k, v in sources_enabled.items() if v}

        if separate_sources and len(sources_enabled) > 1:
            logger.info("Returning separate results for each source...")
            result_dfs: Dict[str, pd.DataFrame] = {}
            for source_name in ("grok", "open_targets", "disgenet"):
                if source_name in sources_enabled:
                    source_df = df.copy()
                    source_df = self._add_enrichment_columns(
                        source_df,
                        payload.get(source_name, {}),
                        gene_column,
                        source_name,
                        hyperlinks,
                    )
                    result_dfs[source_name] = source_df

            merged_df = df.copy()
            merged_df = self._add_enrichment_columns(
                merged_df,
                payload.get("merged", {}),
                gene_column,
                "merged",
                hyperlinks,
            )
            result_dfs["merged"] = merged_df
            return result_dfs

        enriched_df = df.copy()
        enriched_df = self._add_enrichment_columns(
            enriched_df,
            payload.get("merged", {}),
            gene_column,
            "merged",
            hyperlinks,
        )
        return enriched_df
    
    def query_grok_api(
        self,
        gene_names: List[str],
        disease_term: Optional[str] = None
    ) -> Dict[str, Dict]:
        """
        Query Grok API for gene-disease associations.
        
        Args:
            gene_names: List of gene symbols to query
            disease_term: Disease term (defaults to self.disease_term)
            
        Returns:
            Dictionary mapping gene_name -> association info
        """
        if not self.grok_api_key:
            logger.warning("Grok API key not provided. Skipping Grok API queries.")
            return {}
        
        disease_term = disease_term or self.disease_term
        gene_names = self._dedupe_gene_names(gene_names)
        
        cached_results = self._cache_get_batch("grok", gene_names, disease_term)
        uncached_genes = [g for g in gene_names if g.upper() not in cached_results]
        
        if cached_results:
            logger.info(f"Using cache for {len(cached_results)} genes (querying Grok for {len(uncached_genes)} new)")
        
        if not uncached_genes:
            logger.info(f"✅ Retrieved all {len(cached_results)} genes from cache")
            return cached_results
        
        logger.info(f"Querying Grok API for {len(uncached_genes)} genes associated with '{disease_term}'...")

        results = cached_results.copy()
        starting_batch_size = int(self.grok_batch_size)
        batches = [
            uncached_genes[i:i + self.grok_batch_size]
            for i in range(0, len(uncached_genes), self.grok_batch_size)
        ]
        total_batches = len(batches)

        if self.grok_use_xai_batch_api:
            self._query_grok_uncached_xai_batch(batches, disease_term, results)
        else:
            progress = ProgressIndicator(total_batches, "Grok API batches", update_interval=1)
            if self.grok_max_workers == 1 or total_batches == 1:
                remaining_genes = list(uncached_genes)
                batch_num = 0
                current_batch_size = max(1, int(self.grok_batch_size))
                while remaining_genes:
                    batch = remaining_genes[:current_batch_size]
                    remaining_genes = remaining_genes[current_batch_size:]
                    batch_num += 1

                    batch_results, success, used_split_fallback = self._query_grok_batch(
                        batch, disease_term, batch_num
                    )
                    results.update(batch_results)
                    progress.update(success=success)
                    if used_split_fallback and current_batch_size > 1:
                        # Adaptive sizing for the rest of the run: step down gradually to reduce
                        # repeated paid calls that return truncated/invalid JSON.
                        new_batch_size = max(1, current_batch_size - 2)
                        if new_batch_size < current_batch_size:
                            logger.info(
                                "Grok adaptive batching: reducing batch size from %d to %d "
                                "after parse fallback; keeping new size for remaining requests.",
                                current_batch_size,
                                new_batch_size,
                            )
                            current_batch_size = new_batch_size
                            self.grok_batch_size = new_batch_size
                            remaining_batches = (
                                (len(remaining_genes) + current_batch_size - 1) // current_batch_size
                                if remaining_genes
                                else 0
                            )
                            progress.total = progress.current + remaining_batches
                    if not success and self.grok_429_inter_batch_sleep > 0:
                        logger.info(
                            "Waiting %.0fs after failed Grok batch before next batch.",
                            self.grok_429_inter_batch_sleep,
                        )
                        time.sleep(self.grok_429_inter_batch_sleep)
                    elif self.rate_limit_delay > 0:
                        time.sleep(self.rate_limit_delay)
            else:
                with ThreadPoolExecutor(max_workers=min(self.grok_max_workers, total_batches)) as executor:
                    future_to_batch_num = {
                        executor.submit(self._query_grok_batch, batch, disease_term, batch_num): batch_num
                        for batch_num, batch in enumerate(batches, start=1)
                    }
                    for future in as_completed(future_to_batch_num):
                        try:
                            batch_results, success, _used_split_fallback = future.result()
                        except Exception as exc:
                            logger.warning(f"Grok API batch failed unexpectedly: {exc}")
                            batch_results, success = {}, False
                        results.update(batch_results)
                        progress.update(success=success)

        settled_batch_size = int(self.grok_batch_size)
        logger.info(
            "Grok adaptive batch size settled at %d (start: %d)",
            settled_batch_size,
            starting_batch_size,
        )
        logger.info(f"✅ Retrieved disease associations for {len(results)} genes from Grok API ({len(cached_results)} cached, {len(results) - len(cached_results)} new)")
        self._save_disk_cache()
        return results

    def _grok_xai_api_base(self) -> str:
        """Base URL for xAI v1 REST (batches, etc.) derived from chat completions URL."""
        parsed = urlparse(self.grok_api_url)
        if not parsed.scheme or not parsed.netloc:
            return "https://api.x.ai/v1"
        path = (parsed.path or "").rstrip("/")
        if path.endswith("/chat/completions"):
            base_path = path[: -len("/chat/completions")] or "/v1"
        else:
            base_path = "/v1"
        return urlunparse((parsed.scheme, parsed.netloc, base_path, "", "", ""))

    @staticmethod
    def _xai_429_delay_seconds(response: requests.Response, attempt: int) -> float:
        raw = response.headers.get("Retry-After")
        if raw is not None:
            try:
                return float(max(1.0, int(str(raw).strip())))
            except (ValueError, TypeError):
                try:
                    return float(max(1.0, float(str(raw).strip())))
                except (ValueError, TypeError):
                    pass
        return float(min(300.0, 15.0 * (2 ** min(attempt, 5))))

    def _pace_xai_batch_create_slot(self) -> None:
        """Respect xAI limit: at most one batch creation per second per team."""
        cls = type(self)
        with cls._xai_batch_create_lock:
            now = time.monotonic()
            wait = cls._xai_last_batch_create_at + XAI_BATCH_CREATE_MIN_INTERVAL_SEC - now
            if wait > 0:
                logger.info(
                    f"Pausing {wait:.1f}s before creating xAI batch (API limit: 1 batch create/s per team)"
                )
                time.sleep(wait)

    def _mark_xai_batch_created(self) -> None:
        cls = type(self)
        with cls._xai_batch_create_lock:
            cls._xai_last_batch_create_at = time.monotonic()

    def _grok_xai_http_request(
        self,
        session: requests.Session,
        method: str,
        url: str,
        headers: Dict[str, str],
        *,
        json_body: Optional[Dict] = None,
        params: Optional[Dict[str, Union[str, int]]] = None,
        timeout: int = 120,
        context: str = "",
    ) -> requests.Response:
        """POST/GET to xAI with retries on HTTP 429."""
        attempts = max(XAI_BATCH_HTTP_MIN_ATTEMPTS, self.max_retries, 5)
        last: Optional[requests.Response] = None
        for attempt in range(attempts):
            if method.upper() == "POST":
                last = session.post(url, headers=headers, json=json_body, timeout=timeout)
            else:
                last = session.get(url, headers=headers, params=params, timeout=timeout)
            if last.status_code == 429:
                delay = self._xai_429_delay_seconds(last, attempt)
                lbl = context or (urlparse(url).path or "xAI")
                logger.debug(
                    "xAI 429 [%s]: sleeping %.0fs (attempt %d/%d)",
                    lbl,
                    delay,
                    attempt + 1,
                    attempts,
                )
                time.sleep(delay)
                continue
            last.raise_for_status()
            return last
        if last is not None:
            last.raise_for_status()
        raise requests.HTTPError("xAI request failed after retries")

    def _grok_chat_completion_payload(self, prompt: str) -> Dict:
        """Body for realtime chat/completions and xAI Batch `chat_get_completion`."""
        return {
            "model": GROK_COMPLETION_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 2000,
            "stream": False,
        }

    @staticmethod
    def _grok_batch_request_id(batch_num: int, disease_term: str, genes: List[str]) -> str:
        key = f"{batch_num}|{disease_term}|{','.join(g.upper() for g in genes)}"
        digest = hashlib.sha256(key.encode()).hexdigest()[:20]
        return f"b{batch_num}_{digest}"

    def _normalize_xai_batch_result_to_chat_completion(self, item: Dict) -> Dict:
        """Map a single xAI batch results row to the shape expected by _parse_grok_response."""
        if not isinstance(item, dict):
            raise TypeError("batch result item must be a dict")
        err = item.get("error_message") or item.get("error")
        if isinstance(err, str) and err.strip():
            raise ValueError(err.strip())
        if isinstance(err, dict) and err.get("message"):
            raise ValueError(str(err["message"]))

        for key in ("completion_response", "chat_completion", "chat_get_completion_response"):
            inner = item.get(key)
            if isinstance(inner, dict) and inner.get("choices"):
                return inner

        if item.get("choices"):
            return item

        content = item.get("content")
        if isinstance(content, str) and content.strip():
            return {"choices": [{"message": {"content": content}}]}

        resp = item.get("response")
        if isinstance(resp, dict):
            return self._normalize_xai_batch_result_to_chat_completion(resp)

        proto = item.get("proto")
        if isinstance(proto, dict):
            return self._normalize_xai_batch_result_to_chat_completion(proto)

        raise ValueError(f"unrecognized batch result keys: {list(item.keys())}")

    def _xai_batch_poll_until_done(self, batch_id: str) -> None:
        base = self._grok_xai_api_base()
        headers = {
            "Authorization": f"Bearer {self.grok_api_key}",
            "Content-Type": "application/json",
        }
        session = self._get_session()
        short_id = batch_id[:12] + "…" if len(batch_id) > 12 else batch_id
        while True:
            r = self._grok_xai_http_request(
                session,
                "GET",
                f"{base}/batches/{batch_id}",
                headers,
                timeout=120,
                context="batch status poll",
            )
            data = r.json()
            state = data.get("state") or {}
            pending = int(state.get("num_pending", 0) or 0)
            n_ok = int(state.get("num_success", 0) or 0)
            n_err = int(state.get("num_error", 0) or 0)
            n_total = int(state.get("num_requests", 0) or 0)
            logger.info(
                f"xAI Batch {short_id} progress: pending={pending} success={n_ok} error={n_err} total={n_total}"
            )
            if pending == 0:
                break
            time.sleep(self.grok_batch_poll_interval)

    def _xai_batch_collect_result_pages(self, batch_id: str) -> Tuple[List[Dict], List[Dict]]:
        base = self._grok_xai_api_base()
        headers = {"Authorization": f"Bearer {self.grok_api_key}"}
        session = self._get_session()
        succeeded: List[Dict] = []
        failed: List[Dict] = []
        pagination_token: Optional[str] = None
        while True:
            params: Dict[str, Union[str, int]] = {"page_size": 100}
            if pagination_token:
                params["pagination_token"] = pagination_token
            r = self._grok_xai_http_request(
                session,
                "GET",
                f"{base}/batches/{batch_id}/results",
                headers,
                params=params,
                timeout=120,
                context="batch results page",
            )
            page = r.json()
            succeeded.extend(page.get("succeeded") or [])
            failed.extend(page.get("failed") or [])
            pagination_token = page.get("pagination_token")
            if not pagination_token:
                break
        return succeeded, failed

    def _query_grok_uncached_xai_batch(
        self,
        batches: List[List[str]],
        disease_term: str,
        results: Dict[str, Dict],
    ) -> None:
        """Submit gene batches via xAI Batch API, poll, merge into results and disk cache."""
        base = self._grok_xai_api_base()
        headers = {
            "Authorization": f"Bearer {self.grok_api_key}",
            "Content-Type": "application/json",
        }
        session = self._get_session()
        batch_name = f"methyl_mapper_grok_{int(time.time())}"
        self._pace_xai_batch_create_slot()
        cr = self._grok_xai_http_request(
            session,
            "POST",
            f"{base}/batches",
            headers,
            json_body={"name": batch_name},
            timeout=120,
            context="create batch",
        )
        self._mark_xai_batch_created()
        created = cr.json()
        batch_id = created.get("batch_id") or created.get("id")
        if not batch_id and isinstance(created.get("batch"), dict):
            b = created["batch"]
            batch_id = b.get("batch_id") or b.get("id")
        if not batch_id:
            raise ValueError(f"unexpected xAI create batch response keys: {list(created.keys())}")

        rid_to_meta: Dict[str, Tuple[int, List[str]]] = {}
        batch_requests: List[Dict] = []
        for batch_num, gene_batch in enumerate(batches, start=1):
            prompt = self._create_grok_prompt(gene_batch, disease_term)
            rid = self._grok_batch_request_id(batch_num, disease_term, gene_batch)
            rid_to_meta[rid] = (batch_num, gene_batch)
            batch_requests.append(
                {
                    "batch_request_id": rid,
                    "batch_request": {"chat_get_completion": self._grok_chat_completion_payload(prompt)},
                }
            )

        chunk = max(1, self.grok_batch_submit_chunk_size)
        for offset in range(0, len(batch_requests), chunk):
            slice_reqs = batch_requests[offset : offset + chunk]
            ar = self._grok_xai_http_request(
                session,
                "POST",
                f"{base}/batches/{batch_id}/requests",
                headers,
                json_body={"batch_requests": slice_reqs},
                timeout=300,
                context="add batch requests",
            )
            if self.rate_limit_delay > 0 and offset + chunk < len(batch_requests):
                time.sleep(self.rate_limit_delay)

        logger.info(
            f"Submitted {len(batch_requests)} Grok job(s) to xAI Batch API (batch_id={batch_id}); "
            "polling until complete (see https://docs.x.ai/developers/advanced-api-usage/batch-api)"
        )
        self._xai_batch_poll_until_done(batch_id)
        succeeded, failed = self._xai_batch_collect_result_pages(batch_id)

        result_by_rid: Dict[str, Dict] = {}
        failed_rids: set[str] = set()
        for item in succeeded:
            rid = item.get("batch_request_id") or item.get("custom_id")
            if isinstance(rid, str) and rid:
                result_by_rid[rid] = item
        for item in failed:
            rid = item.get("batch_request_id") or item.get("custom_id")
            if isinstance(rid, str) and rid:
                failed_rids.add(rid)
                result_by_rid[rid] = item

        progress = ProgressIndicator(len(rid_to_meta), "Grok xAI batch", update_interval=1)

        def _merge_batch_result(gene_batch: List[str], batch_num: int, completion: Dict) -> Tuple[Dict[str, Dict], bool]:
            batch_results, used_fallback = self._parse_grok_response(completion, gene_batch)
            if used_fallback and len(gene_batch) > 1:
                batch_results, ok, _ = self._query_grok_batch(gene_batch, disease_term, batch_num)
                return batch_results, ok
            for gene_name, association_info in batch_results.items():
                self._cache_set(self._cache_key("grok", gene_name, disease_term), association_info)
            return batch_results, True

        for rid, (batch_num, gene_batch) in rid_to_meta.items():
            item = result_by_rid.get(rid)
            if item is None:
                logger.warning(
                    f"xAI batch: no result for {rid}; falling back to realtime for {len(gene_batch)} genes"
                )
                batch_results, ok, _ = self._query_grok_batch(gene_batch, disease_term, batch_num)
                results.update(batch_results)
                progress.update(success=ok)
                continue
            if rid in failed_rids:
                err_txt = item.get("error_message") or item.get("error") or item
                logger.warning(f"xAI batch request failed {rid}: {err_txt}")
                batch_results, ok, _ = self._query_grok_batch(gene_batch, disease_term, batch_num)
                results.update(batch_results)
                progress.update(success=ok)
                continue
            try:
                completion = self._normalize_xai_batch_result_to_chat_completion(item)
                batch_results, ok = _merge_batch_result(gene_batch, batch_num, completion)
                results.update(batch_results)
                progress.update(success=ok)
            except Exception as exc:
                logger.warning(f"xAI batch result failed for {rid} ({len(gene_batch)} genes): {exc}")
                batch_results, ok, _ = self._query_grok_batch(gene_batch, disease_term, batch_num)
                results.update(batch_results)
                progress.update(success=ok)

    def _query_grok_batch(
        self,
        batch: List[str],
        disease_term: str,
        batch_num: int,
    ) -> Tuple[Dict[str, Dict], bool, bool]:
        """Query one Grok batch with retries and batch-local caching.
        On JSON parse fallback (e.g. truncated/invalid JSON), recursively split into
        smaller batches until parsing succeeds or the batch size reaches one gene.
        """
        prompt = self._create_grok_prompt(batch, disease_term)
        last_error = None
        base_timeout = 60
        per_gene_timeout = 20

        for attempt in range(self.max_retries):
            try:
                timeout = max(base_timeout, base_timeout + len(batch) * per_gene_timeout)
                if attempt > 0:
                    timeout = int(timeout * (1.5 ** attempt))
                logger.debug(
                    f"Querying batch {batch_num} with {len(batch)} genes "
                    f"(timeout: {timeout}s, attempt {attempt + 1}/{self.max_retries})"
                )
                response = self._call_grok_api(prompt, timeout=timeout)
                batch_results, used_fallback = self._parse_grok_response(response, batch)

                if used_fallback and len(batch) > 1:
                    mid = len(batch) // 2
                    sub_batch_a, sub_batch_b = batch[:mid], batch[mid:]
                    logger.info(
                        f"Grok batch {batch_num}: JSON parse failed (response likely too large). "
                        f"Retrying with smaller batches: {len(sub_batch_a)} + {len(sub_batch_b)} genes"
                    )
                    results_a, ok_a, _ = self._query_grok_batch(sub_batch_a, disease_term, batch_num)
                    results_b, ok_b, _ = self._query_grok_batch(sub_batch_b, disease_term, batch_num)
                    merged = {**results_a, **results_b}
                    return merged, ok_a and ok_b, True

                for gene_name, association_info in batch_results.items():
                    self._cache_set(self._cache_key("grok", gene_name, disease_term), association_info)
                return batch_results, True, False
            except requests.HTTPError as exc:
                last_error = exc
                if attempt < self.max_retries - 1:
                    # Use longer backoff for 429 (rate limit) and respect Retry-After if present
                    if exc.response is not None and exc.response.status_code == 429:
                        raw_retry_after = exc.response.headers.get("Retry-After")
                        if raw_retry_after:
                            try:
                                delay = float(raw_retry_after)
                            except (ValueError, TypeError):
                                delay = 120.0 * (2 ** attempt)
                        else:
                            # xAI limits often need multi-minute recovery; grow quickly
                            delay = float(min(900.0, 120.0 * (2 ** attempt)))
                        delay = max(60.0, delay)
                        if attempt == 0:
                            logger.warning(
                                "Grok API: 429 Too Many Requests (batch %d)",
                                batch_num,
                            )
                    else:
                        delay = (2 ** attempt) * 5
                    logger.debug(
                        "Grok batch %d: %s (attempt %d/%d); retry in %.0fs",
                        batch_num,
                        exc,
                        attempt + 1,
                        self.max_retries,
                        delay,
                    )
                    time.sleep(delay)
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries - 1:
                    delay = (2 ** attempt) * 5
                    logger.debug(
                        "Grok batch %d: %s (attempt %d/%d); retry in %.0fs",
                        batch_num,
                        exc,
                        attempt + 1,
                        self.max_retries,
                        delay,
                    )
                    time.sleep(delay)

        logger.warning(
            f"Grok API query failed for batch {batch_num} after {self.max_retries} attempts: {last_error}"
        )
        return {}, False, False
    
    def _create_grok_prompt(self, gene_names: List[str], disease_term: str) -> str:
        """Create a prompt for Grok: biological annotation (not primary disease-evidence scoring)."""
        genes_str = ", ".join(gene_names)
        prompt = f"""You are a biomedical curator. For each gene symbol below, provide concise **annotation** useful for interpreting differential DNA methylation in the context of "{disease_term}".

Genes: {genes_str}

Return ONLY a valid JSON array. For each gene include:
1. "gene_name": symbol exactly as listed
2. "gene_basic_description": one-sentence molecular/cellular function (required when known)
3. "functional_role": short note on plausible relevance to {disease_term} or methylation regulation (or null)
4. "description": optional extra context (pathways, tissue, regulatory role) (or null)
5. "associated": boolean — true if you judge any plausible link to {disease_term} worth flagging for a human reviewer; false otherwise (this is secondary to external evidence databases)
6. "association_type": one of ["direct","indirect","predicted","none"]
7. "evidence_level": one of ["high","medium","low","none"] for your qualitative judgment only
8. "publications": integer estimate or 0

Keep each text field concise:
- gene_basic_description: <= 20 words
- functional_role: <= 20 words
- description: <= 25 words

Example:
[
  {{"gene_name": "GENE1", "gene_basic_description": "DNA methyltransferase maintaining CpG methylation.", "functional_role": "May shape tumor epigenome.", "description": null, "associated": true, "association_type": "indirect", "evidence_level": "medium", "publications": 0}}
]
"""
        return prompt
    
    def _call_grok_api(self, prompt: str, timeout: int = 30) -> Dict:
        """
        Call Grok API and return response.
        
        Args:
            prompt: The prompt to send to the API
            timeout: Request timeout in seconds (default: 30)
        """
        headers = {
            "Authorization": f"Bearer {self.grok_api_key}",
            "Content-Type": "application/json"
        }
        
        payload = self._grok_chat_completion_payload(prompt)
        
        # Throttle: at most one request start per rate_limit_delay (avoids 429 when using multiple workers)
        with self._grok_request_lock:
            now = time.monotonic()
            wait = max(0.0, self._grok_next_start_time - now)
            self._grok_next_start_time = now + wait + self.rate_limit_delay
        if wait > 0:
            time.sleep(wait)
        logger.debug(f"Calling Grok API with timeout={timeout}s for prompt length={len(prompt)}")
        session = self._get_session()
        response = session.post(
            self.grok_api_url,
            headers=headers,
            json=payload,
            timeout=timeout
        )
        if response.status_code == 400 and "max_tokens" in payload:
            # Compatibility fallback: some xAI model revisions prefer max_output_tokens.
            fallback_payload = dict(payload)
            fallback_payload.pop("max_tokens", None)
            fallback_payload["max_output_tokens"] = payload["max_tokens"]
            logger.debug("Grok API 400 on primary payload; retrying with max_output_tokens fallback")
            response = session.post(
                self.grok_api_url,
                headers=headers,
                json=fallback_payload,
                timeout=timeout,
            )

        if response.status_code >= 400:
            detail = ""
            try:
                body = response.json()
                if isinstance(body, dict):
                    err = body.get("error")
                    if isinstance(err, dict):
                        detail = str(err.get("message") or err)
                    elif err is not None:
                        detail = str(err)
                    else:
                        detail = str(body)
                else:
                    detail = str(body)
            except Exception:
                detail = (response.text or "").strip()
            msg = f"xAI error {response.status_code}: {detail or 'no response body'}"
            raise requests.HTTPError(msg, response=response)

        return response.json()
    
    def _normalize_associated(self, value: Union[bool, str, int, None]) -> bool:
        """Normalize Grok's 'associated' field to bool (handles string 'true'/'yes'/'1' etc.)."""
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        s = str(value).strip().lower()
        if s in ("true", "yes", "1", "y"):
            return True
        if s in ("false", "no", "0", "n", "none", ""):
            return False
        # Any other non-empty string that suggests association
        return len(s) > 0 and s not in ("no association", "not associated", "unrelated")

    def _extract_json_array_from_content(self, content: str) -> List[Dict]:
        """Extract a JSON array from API response (handles markdown, object wrapper, raw array)."""
        import re
        # Strip markdown code blocks (```json ... ``` or ``` ... ```)
        stripped = content.strip()
        if stripped.startswith("```json"):
            stripped = stripped[7:].lstrip()
        elif stripped.startswith("```"):
            stripped = stripped[3:].lstrip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()
        stripped = stripped.strip()
        # Try parse full content as JSON
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                for key in ("genes", "associations", "results", "data", "items"):
                    if key in parsed and isinstance(parsed[key], list):
                        return parsed[key]
                # Any first list value
                for v in parsed.values():
                    if isinstance(v, list):
                        return v
        except json.JSONDecodeError:
            pass
        # Find first top-level JSON array by bracket matching
        start = stripped.find("[")
        if start == -1:
            raise ValueError("No JSON array found in response")
        depth = 0
        in_string = None
        escape = False
        for i, c in enumerate(stripped[start:], start=start):
            if escape:
                escape = False
                continue
            if c == "\\" and in_string:
                escape = True
                continue
            if in_string:
                if c == in_string:
                    in_string = None
                continue
            if c in ('"', "'"):
                in_string = c
                continue
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    json_str = stripped[start : i + 1]
                    return json.loads(json_str)
        raise ValueError("Unclosed JSON array in response")

    def _parse_grok_response(self, response: Dict, gene_names: List[str]) -> Tuple[Dict[str, Dict], bool]:
        """Parse Grok API response into gene-disease associations.
        Returns (results, used_fallback). used_fallback is True when JSON parsing failed and text fallback was used.
        """
        import re
        
        results = {}
        used_fallback = False
        
        try:
            # Extract content from response
            # Grok API response format: {'choices': [{'message': {'content': '...'}}]}
            content = ""
            if 'choices' in response and len(response['choices']) > 0:
                content = response['choices'][0].get('message', {}).get('content', '')
            elif 'content' in response:
                content = response['content']
            else:
                logger.warning(f"Unexpected Grok API response format: {list(response.keys())}")
                content = str(response)
            
            if not content:
                raise ValueError("Empty response from Grok API")
            
            try:
                associations = self._extract_json_array_from_content(content)
            except (ValueError, json.JSONDecodeError) as e:
                logger.warning(f"Could not extract JSON array from Grok response: {e}")
                used_fallback = True
                # Fallback: infer from text
                associations = []
                content_upper = content.upper()
                for gene in gene_names:
                    gene_upper = gene.upper()
                    if gene_upper in content_upper:
                        associations.append({
                            'gene_name': gene_upper,
                            'associated': True,
                            'association_type': 'predicted',
                            'evidence_level': 'low',
                            'description': "Mentioned in context of disease",
                            'publications': 0,
                            'functional_role': None,
                            'gene_basic_description': None
                        })
                    else:
                        associations.append({
                            'gene_name': gene_upper,
                            'associated': False,
                            'association_type': 'none',
                            'evidence_level': 'none',
                            'description': None,
                            'publications': 0,
                            'functional_role': None,
                            'gene_basic_description': None
                        })
            
            if not isinstance(associations, list):
                associations = []
            
            # Convert to dictionary keyed by gene_name; normalize 'associated' (Grok may return string)
            for assoc in associations:
                if not isinstance(assoc, dict):
                    continue
                gene_name = str(assoc.get('gene_name', assoc.get('gene', ''))).strip().upper()
                if not gene_name:
                    continue
                raw_associated = assoc.get('associated', assoc.get('is_associated', assoc.get('has_association', False)))
                results[gene_name] = {
                    'associated': self._normalize_associated(raw_associated),
                    'association_type': str(assoc.get('association_type', 'none')).lower() or 'none',
                    'evidence_level': str(assoc.get('evidence_level', 'none')).lower() or 'none',
                    'description': assoc.get('description'),
                    'publications': int(assoc.get('publications', 0)) if assoc.get('publications') is not None else 0,
                    'functional_role': assoc.get('functional_role'),
                    'gene_basic_description': assoc.get('gene_basic_description'),
                    'source': 'grok_api'
                }
            
            # Fill in missing genes as "not associated"
            for gene in gene_names:
                gene_upper = gene.upper()
                if gene_upper not in results:
                    results[gene_upper] = {
                        'associated': False,
                        'association_type': 'none',
                        'evidence_level': 'none',
                        'description': None,
                        'publications': 0,
                        'functional_role': None,
                        'gene_basic_description': None,
                        'source': 'grok_api'
                    }
            
            n_associated = sum(1 for r in results.values() if r.get('associated'))
            logger.info(f"Grok API parse: {n_associated}/{len(results)} genes marked associated with disease")
                    
        except (KeyError, IndexError, TypeError, ValueError) as e:
            logger.warning(f"Failed to parse Grok API response: {e}")
            used_fallback = True
            if 'content' in locals():
                logger.debug(f"Response content (first 500 chars): {content[:500]}")
            for gene in gene_names:
                results[gene.upper()] = {
                    'associated': False,
                    'association_type': 'none',
                    'evidence_level': 'none',
                    'description': None,
                    'publications': 0,
                    'functional_role': None,
                    'gene_basic_description': None,
                    'source': 'grok_api_parse_error'
                }
        
        return results, used_fallback
    
    def query_disgenet(
        self,
        gene_names: List[str],
        disease_term: Optional[str] = None
    ) -> Dict[str, Dict]:
        """
        Query DisGeNET database for gene-disease associations (fallback).
        
        Args:
            gene_names: List of gene symbols
            disease_term: Disease term (used for filtering)
            
        Returns:
            Dictionary mapping gene_name -> association info
        """
        if not self.use_disgenet or not self.disgenet_api_key:
            if not self.use_disgenet:
                logger.debug("DisGeNET queries disabled")
            else:
                logger.warning("DisGeNET API key not available")
            return {}

        disease_term = disease_term or self.disease_term
        gene_names = self._dedupe_gene_names(gene_names)

        cached_results = self._cache_get_batch("disgenet", gene_names, disease_term)
        uncached_genes = [g for g in gene_names if g.upper() not in cached_results]

        if cached_results:
            logger.debug(f"Found {len(cached_results)} genes in cache")

        if not uncached_genes:
            logger.info(f"✅ Retrieved all {len(cached_results)} genes from cache")
            return cached_results

        logger.info(f"Querying DisGeNET for {len(uncached_genes)} genes...")

        disgenet_api_key = self.disgenet_api_key

        results = cached_results.copy()
        progress = ProgressIndicator(len(uncached_genes), "DisGeNET genes", update_interval=50)

        if self.disgenet_max_workers == 1 or len(uncached_genes) == 1:
            for gene in uncached_genes:
                gene_upper, association_info, success = self._query_disgenet_gene(gene, disease_term, disgenet_api_key)
                results[gene_upper] = association_info
                progress.update(success=success)
        else:
            with ThreadPoolExecutor(max_workers=min(self.disgenet_max_workers, len(uncached_genes))) as executor:
                future_to_gene = {
                    executor.submit(self._query_disgenet_gene, gene, disease_term, disgenet_api_key): gene
                    for gene in uncached_genes
                }
                for future in as_completed(future_to_gene):
                    gene = future_to_gene[future]
                    try:
                        gene_upper, association_info, success = future.result()
                    except Exception as exc:
                        logger.debug(f"DisGeNET query failed for {gene}: {exc}")
                        gene_upper = gene.upper()
                        association_info = {
                            'associated': False,
                            'association_type': 'none',
                            'evidence_level': 'none',
                            'description': None,
                            'publications': 0,
                            'functional_role': None,
                            'source': 'disgenet_error'
                        }
                        self._cache_set(self._cache_key("disgenet", gene, disease_term), association_info)
                        success = False
                    results[gene_upper] = association_info
                    progress.update(success=success)
        
        logger.info(f"✅ Retrieved associations for {len(results)} genes from DisGeNET ({len(cached_results)} cached, {len(results) - len(cached_results)} new)")
        self._save_disk_cache()
        return results

    def _query_disgenet_gene(
        self,
        gene: str,
        disease_term: str,
        disgenet_api_key: str,
    ) -> Tuple[str, Dict, bool]:
        """Query DisGeNET for one gene symbol."""
        gene_upper = gene.upper()
        base_url = "https://api.disgenet.com/api/v1/gda/gene/"
        try:
            url = f"{base_url}{gene}"
            headers = {"Authorization": f"Bearer {disgenet_api_key}"}
            response = self._get_session().get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()

            if disease_term:
                disease_lower = disease_term.lower()
                relevant_associations = [
                    entry for entry in data
                    if disease_lower in entry.get('disease_name', '').lower()
                    or disease_lower in entry.get('disease_class', '').lower()
                ]
            else:
                relevant_associations = data

            if relevant_associations:
                best = max(relevant_associations, key=lambda x: x.get('score', 0))
                association_info = {
                    'associated': True,
                    'association_type': 'database',
                    'evidence_level': 'high' if best.get('score', 0) > 0.5 else 'medium',
                    'description': best.get('disease_name'),
                    'publications': best.get('nof_pmids', 0),
                    'functional_role': best.get('disease_class'),
                    'source': 'disgenet',
                    'score': best.get('score', 0),
                }
            else:
                association_info = {
                    'associated': False,
                    'association_type': 'none',
                    'evidence_level': 'none',
                    'description': None,
                    'publications': 0,
                    'functional_role': None,
                    'source': 'disgenet',
                }
            self._cache_set(self._cache_key("disgenet", gene, disease_term), association_info)
            return gene_upper, association_info, True
        except Exception as exc:
            logger.debug(f"DisGeNET query failed for {gene}: {exc}")
            association_info = {
                'associated': False,
                'association_type': 'none',
                'evidence_level': 'none',
                'description': None,
                'publications': 0,
                'functional_role': None,
                'source': 'disgenet_error'
            }
            self._cache_set(self._cache_key("disgenet", gene, disease_term), association_info)
            return gene_upper, association_info, False

    def query_open_targets(
        self,
        gene_names: List[str],
        disease_term: Optional[str] = None
    ) -> Dict[str, Dict]:
        """
        Query Open Targets for gene-disease associations.
        """
        if not self.use_open_targets:
            logger.debug("Open Targets queries disabled")
            return {}

        disease_term = disease_term or self.disease_term
        gene_names = self._dedupe_gene_names(gene_names)

        # Resolve disease ID once per call
        disease_id = self._resolve_open_targets_disease_id(disease_term)
        if not disease_id:
            logger.warning(f"Open Targets: no disease match for '{disease_term}'")
            return {}

        cached_results = self._cache_get_batch("open_targets", gene_names, disease_term)
        uncached_genes = [g for g in gene_names if g.upper() not in cached_results]

        if cached_results:
            logger.debug(f"Found {len(cached_results)} Open Targets genes in cache")

        if not uncached_genes:
            logger.info(f"✅ Retrieved all {len(cached_results)} genes from Open Targets cache")
            return cached_results

        logger.info(f"Querying Open Targets for {len(uncached_genes)} genes...")
        results = cached_results.copy()

        progress = ProgressIndicator(len(uncached_genes), "Open Targets genes", update_interval=50)

        if self.open_targets_max_workers == 1 or len(uncached_genes) == 1:
            for gene in uncached_genes:
                gene_upper, association_info, success = self._query_open_targets_gene(gene, disease_term, disease_id)
                results[gene_upper] = association_info
                progress.update(success=success)
        else:
            with ThreadPoolExecutor(max_workers=min(self.open_targets_max_workers, len(uncached_genes))) as executor:
                future_to_gene = {
                    executor.submit(self._query_open_targets_gene, gene, disease_term, disease_id): gene
                    for gene in uncached_genes
                }
                for future in as_completed(future_to_gene):
                    gene = future_to_gene[future]
                    try:
                        gene_upper, association_info, success = future.result()
                    except Exception as exc:
                        logger.debug(f"Open Targets query failed for {gene}: {exc}")
                        gene_upper = gene.upper()
                        association_info = {
                            'associated': False,
                            'association_type': 'none',
                            'evidence_level': 'none',
                            'description': None,
                            'publications': 0,
                            'functional_role': None,
                            'source': 'open_targets_error'
                        }
                        self._cache_set(self._cache_key("open_targets", gene, disease_term), association_info)
                        success = False
                    results[gene_upper] = association_info
                    progress.update(success=success)

        logger.info(f"✅ Retrieved associations for {len(results)} genes from Open Targets ({len(cached_results)} cached, {len(results) - len(cached_results)} new)")
        self._save_disk_cache()
        return results

    def _query_open_targets_gene(
        self,
        gene: str,
        disease_term: str,
        disease_id: str,
    ) -> Tuple[str, Dict, bool]:
        """Query Open Targets for one gene symbol."""
        gene_upper = gene.upper()
        try:
            target_id = self._resolve_open_targets_target_id(gene)
            if not target_id:
                association_info = {
                    'associated': False,
                    'association_type': 'none',
                    'evidence_level': 'none',
                    'description': None,
                    'publications': 0,
                    'functional_role': None,
                    'source': 'open_targets'
                }
            else:
                assoc = self._fetch_open_targets_association(target_id, disease_id)
                association_info = assoc or {
                    'associated': False,
                    'association_type': 'none',
                    'evidence_level': 'none',
                    'description': None,
                    'publications': 0,
                    'functional_role': None,
                    'source': 'open_targets'
                }
            self._cache_set(self._cache_key("open_targets", gene, disease_term), association_info)
            return gene_upper, association_info, True
        except Exception as exc:
            logger.debug(f"Open Targets query failed for {gene}: {exc}")
            association_info = {
                'associated': False,
                'association_type': 'none',
                'evidence_level': 'none',
                'description': None,
                'publications': 0,
                'functional_role': None,
                'source': 'open_targets_error'
            }
            self._cache_set(self._cache_key("open_targets", gene, disease_term), association_info)
            return gene_upper, association_info, False
    
    def enrich_gene_dataframe(
        self,
        df: pd.DataFrame,
        gene_column: str = 'gene_name',
        disease_term: Optional[str] = None,
        separate_sources: bool = False
    ) -> Union[pd.DataFrame, Dict[str, pd.DataFrame]]:
        """
        Enrich a DataFrame with disease associations.

        Args:
            df: DataFrame with gene information
            gene_column: Column name containing gene symbols
            disease_term: Disease term to search for
            separate_sources: If True and both sources enabled, return dict with separate DataFrames

        Returns:
            DataFrame with added disease association columns, or dict of DataFrames if separate_sources=True
        """
        if gene_column not in df.columns:
            raise ValueError(f"Column '{gene_column}' not found in DataFrame")
        
        unique_genes = self._dedupe_gene_names(df[gene_column].dropna().unique().tolist())
        
        if not unique_genes:
            logger.warning("No genes found in DataFrame; adding disease columns with empty values.")
            payload = self._build_enrichment_payload([])
            return self._apply_enrichment_payload(df.copy(), gene_column, payload, separate_sources=False)

        payload = self._build_enrichment_payload(unique_genes, disease_term)
        enriched_df = self._apply_enrichment_payload(df.copy(), gene_column, payload, separate_sources=separate_sources)
        if isinstance(enriched_df, dict):
            merged_df = enriched_df['merged']
            n_associated = merged_df['disease_associated'].sum()
            logger.info(
                f"✅ Enriched DataFrame: {n_associated}/{len(merged_df)} genes associated with "
                f"'{disease_term or self.disease_term}'"
            )
            return enriched_df

        # Log summary
        n_associated = enriched_df['disease_associated'].sum()
        logger.info(f"✅ Enriched DataFrame: {n_associated}/{len(enriched_df)} genes associated with '{disease_term or self.disease_term}'")

        return enriched_df

    @staticmethod
    def _compose_grok_annotation(grok_entry: Dict) -> Optional[str]:
        parts = []
        for key in ("gene_basic_description", "functional_role", "description"):
            val = grok_entry.get(key)
            if val is not None and str(val).strip():
                parts.append(str(val).strip())
        return " | ".join(parts) if parts else None

    def _merge_results(
        self,
        grok_results: Dict[str, Dict],
        open_targets_results: Dict[str, Dict],
        disgenet_results: Dict[str, Dict],
        unique_genes: List[str]
    ) -> Dict[str, Dict]:
        """Merge results from multiple sources with priority logic."""
        merged_results: Dict[str, Dict] = {}

        if self.use_grok and self.use_open_targets:
            for gene in unique_genes:
                gene_upper = gene.upper()
                ot = open_targets_results.get(gene_upper, {})
                gr = grok_results.get(gene_upper, {})
                dg = disgenet_results.get(gene_upper, {})
                base = {
                    "associated": bool(ot.get("associated", False)),
                    "association_type": str(ot.get("association_type", "none") or "none").lower(),
                    "evidence_level": str(ot.get("evidence_level", "none") or "none").lower(),
                    "description": ot.get("description"),
                    "publications": int(ot.get("publications", 0) or 0),
                    "functional_role": ot.get("functional_role"),
                    "score": float(ot.get("score", 0.0) or 0.0),
                    "source": ot.get("source") or "open_targets",
                    "gene_basic_description": gr.get("gene_basic_description"),
                    "grok_annotation_summary": self._compose_grok_annotation(gr),
                }
                if (
                    not base["associated"]
                    and self.use_disgenet
                    and self._association_meets_thresholds(dg)
                ):
                    base.update({
                        "associated": bool(dg.get("associated", False)),
                        "association_type": str(dg.get("association_type", "none") or "none").lower(),
                        "evidence_level": str(dg.get("evidence_level", "none") or "none").lower(),
                        "description": dg.get("description", base["description"]),
                        "publications": int(dg.get("publications", 0) or 0),
                        "functional_role": dg.get("functional_role", base["functional_role"]),
                        "score": float(dg.get("score", 0.0) or 0.0),
                        "source": dg.get("source", "disgenet"),
                    })
                merged_results[gene_upper] = base
            return merged_results

        for gene in unique_genes:
            gene_upper = gene.upper()
            grok_entry = grok_results.get(gene_upper, {})

            if gene_upper in grok_results and self._association_meets_thresholds(grok_results[gene_upper]):
                merged_results[gene_upper] = dict(grok_results[gene_upper])
            elif gene_upper in open_targets_results and self._association_meets_thresholds(open_targets_results[gene_upper]):
                merged_results[gene_upper] = dict(open_targets_results[gene_upper])
            elif gene_upper in disgenet_results and self._association_meets_thresholds(disgenet_results[gene_upper]):
                merged_results[gene_upper] = dict(disgenet_results[gene_upper])
            elif gene_upper in grok_results:
                merged_results[gene_upper] = dict(grok_results[gene_upper])
            elif gene_upper in open_targets_results:
                merged_results[gene_upper] = dict(open_targets_results[gene_upper])
            elif gene_upper in disgenet_results:
                merged_results[gene_upper] = dict(disgenet_results[gene_upper])
            else:
                merged_results[gene_upper] = {
                    'associated': False,
                    'association_type': 'none',
                    'evidence_level': 'none',
                    'description': None,
                    'publications': 0,
                    'functional_role': None,
                    'gene_basic_description': grok_entry.get('gene_basic_description'),
                    'source': 'none',
                    'score': 0.0,
                }

            if merged_results[gene_upper].get('gene_basic_description') is None and grok_entry.get('gene_basic_description'):
                merged_results[gene_upper]['gene_basic_description'] = grok_entry['gene_basic_description']

            if merged_results[gene_upper].get('score') is None or merged_results[gene_upper].get('score') == 0:
                ot_score = open_targets_results.get(gene_upper, {}).get('score')
                if ot_score is not None and float(ot_score) != 0:
                    merged_results[gene_upper]['score'] = float(ot_score)
                elif merged_results[gene_upper].get('score') is None:
                    merged_results[gene_upper]['score'] = 0.0
        return merged_results

    def _association_meets_thresholds(self, assoc: Dict) -> bool:
        """Determine if association meets evidence thresholds."""
        if not assoc or not assoc.get('associated', False):
            return False

        assoc_type = str(assoc.get('association_type', 'none')).lower()
        if not self.allow_predicted and assoc_type == 'predicted':
            return False

        evidence_level = str(assoc.get('evidence_level', 'none')).lower()
        if EVIDENCE_LEVEL_ORDER.get(evidence_level, 0) < EVIDENCE_LEVEL_ORDER[self.min_evidence_level]:
            return False

        publications = assoc.get('publications', 0) or 0
        if publications < self.min_publications:
            return False

        if assoc.get('source') == 'disgenet':
            score = assoc.get('score', 0.0) or 0.0
            if score < self.min_disgenet_score:
                return False
        if assoc.get('source') == 'open_targets':
            score = assoc.get('score', 0.0) or 0.0
            if score < self.min_open_targets_score:
                return False

        return True

    def _add_enrichment_columns(self, df: pd.DataFrame, results: Dict[str, Dict], gene_column: str, source_prefix: str, hyperlinks: Dict[str, Dict]) -> pd.DataFrame:
        """Add enrichment columns to DataFrame with hyperlinks."""
        # Disease association columns
        df['disease_associated_raw'] = df[gene_column].str.upper().map(
            lambda x: results.get(x, {}).get('associated', False) if pd.notna(x) else False
        )
        df['disease_associated'] = df[gene_column].str.upper().map(
            lambda x: self._association_meets_thresholds(results.get(x, {})) if pd.notna(x) else False
        )
        df['disease_association_type'] = df[gene_column].str.upper().map(
            lambda x: results.get(x, {}).get('association_type', 'none') if pd.notna(x) else 'none'
        )
        df['disease_evidence_level'] = df[gene_column].str.upper().map(
            lambda x: results.get(x, {}).get('evidence_level', 'none') if pd.notna(x) else 'none'
        )
        df['disease_description'] = df[gene_column].str.upper().map(
            lambda x: results.get(x, {}).get('description') if pd.notna(x) else None
        )
        df['disease_publications'] = df[gene_column].str.upper().map(
            lambda x: results.get(x, {}).get('publications', 0) if pd.notna(x) else 0
        )
        df['disease_functional_role'] = df[gene_column].str.upper().map(
            lambda x: results.get(x, {}).get('functional_role') if pd.notna(x) else None
        )
        df['disease_source'] = df[gene_column].str.upper().map(
            lambda x: results.get(x, {}).get('source', 'none') if pd.notna(x) else 'none'
        )
        df['disease_score'] = df[gene_column].str.upper().map(
            lambda x: results.get(x, {}).get('score', 0.0) if pd.notna(x) else 0.0
        )

        # Add hyperlinks
        df['gene_ncbi_link'] = df[gene_column].str.upper().map(
            lambda x: hyperlinks.get(x, {}).get('ncbi', '') if pd.notna(x) else ''
        )
        df['gene_ensembl_link'] = df[gene_column].str.upper().map(
            lambda x: hyperlinks.get(x, {}).get('ensembl', '') if pd.notna(x) else ''
        )
        df['gene_uniprot_link'] = df[gene_column].str.upper().map(
            lambda x: hyperlinks.get(x, {}).get('uniprot', '') if pd.notna(x) else ''
        )
        df['gene_omim_link'] = df[gene_column].str.upper().map(
            lambda x: hyperlinks.get(x, {}).get('omim', '') if pd.notna(x) else ''
        )

        # gene_basic_description: only from Grok (Open Targets does not provide it)
        df['gene_basic_description'] = df[gene_column].str.upper().map(
            lambda x: (results.get(x, {}).get('gene_basic_description') or '') if pd.notna(x) else ''
        )
        df['grok_annotation_summary'] = df[gene_column].str.upper().map(
            lambda x: (results.get(x, {}).get('grok_annotation_summary') or '') if pd.notna(x) else ''
        )
        # If no gene has a description (all empty or NaN), remove the column from export
        if 'gene_basic_description' in df.columns:
            filled = df['gene_basic_description'].fillna('').astype(str).str.strip()
            if (filled == '').all():
                df.drop(columns=['gene_basic_description'], inplace=True)
        if 'grok_annotation_summary' in df.columns:
            filled_g = df['grok_annotation_summary'].fillna('').astype(str).str.strip()
            if (filled_g == '').all():
                df.drop(columns=['grok_annotation_summary'], inplace=True)

        return df

    def _generate_gene_hyperlinks(self, gene_names: List[str]) -> Dict[str, Dict]:
        """Generate hyperlinks for gene databases (NCBI, Ensembl, UniProt, OMIM)."""
        hyperlinks = {}
        for gene in gene_names:
            gene_upper = gene.upper()
            hyperlinks[gene_upper] = {
                'ncbi': f'https://www.ncbi.nlm.nih.gov/gene/?term={gene}',
                'ensembl': f'https://www.ensembl.org/id/{gene}',
                'uniprot': f'https://www.uniprot.org/uniprotkb?query={gene}',
                'omim': f'https://www.omim.org/search?search={gene}',
            }
        return hyperlinks

    def _cache_key(self, source: str, gene_name: str, disease_term: str) -> str:
        """Build a cache key with source separation."""
        return f"{source}:{gene_name.upper()}:{disease_term}"

    def _cache_parse_key(self, key: str) -> tuple:
        """Parse cache key into (source, gene, disease_term)."""
        parts = key.split(":", 2)
        if len(parts) != 3:
            return ("", "", "")
        return (parts[0], parts[1].upper(), parts[2])

    def _cache_get(self, key: str) -> Optional[Dict]:
        """Retrieve a cached association, honoring TTL."""
        source, gene, disease_term = self._cache_parse_key(key)
        if not source:
            return None
        return self._cache_get_batch(source, [gene], disease_term).get(gene)

    def _cache_get_batch(
        self, source: str, gene_names: List[str], disease_term: str
    ) -> Dict[str, Dict]:
        """Batch lookup with same-run memoization plus optional disk-backed reuse."""
        if not gene_names:
            return {}
        result = {}
        missing_for_disk: List[str] = []
        with self._cache_lock:
            for gene_name in gene_names:
                gene_upper = str(gene_name).strip().upper()
                if not gene_upper or gene_upper in result:
                    continue
                key = self._cache_key(source, gene_upper, disease_term)

                runtime_value = self._association_runtime_cache.get(key)
                if isinstance(runtime_value, dict):
                    if self._is_empty_association_payload(runtime_value, source=source):
                        self._association_runtime_cache.pop(key, None)
                    else:
                        result[gene_upper] = runtime_value
                        continue
                missing_for_disk.append(gene_upper)

            if self.cache_enabled and self.cache_store is not None and missing_for_disk:
                disk_rows = self.cache_store.fetch_associations_batch(source, missing_for_disk, disease_term)
                for gene_upper in missing_for_disk:
                    disk_entry = disk_rows.get(gene_upper)
                    if not disk_entry:
                        continue
                    ts = disk_entry.get("ts")
                    ts = float(ts) if ts is not None else None
                    if not self._is_cache_valid(ts, source):
                        continue
                    value = disk_entry.get("value")
                    if isinstance(value, dict):
                        if self._is_empty_association_payload(value, source=source):
                            continue
                        key = self._cache_key(source, gene_upper, disease_term)
                        result[gene_upper] = value
                        self._association_runtime_cache[key] = value
        return result

    @staticmethod
    def _is_empty_association_payload(value: Dict, source: Optional[str] = None) -> bool:
        """
        Treat placeholder/failed payloads as cache-miss candidates.

        This handles historical entries where a source row exists but contains only
        empty defaults (None/0/false/'none'), which should be refreshed from source.
        """
        if not isinstance(value, dict) or not value:
            return True

        # Grok can legitimately return explicit negatives (associated=False). Those
        # are valid decisions and should be reused from cache to avoid re-querying
        # every "not associated" gene on subsequent runs.
        if str(source or "").strip().lower() == "grok" and "associated" in value:
            return False

        ignored_keys = {"source", "gene", "gene_name", "disease_term", "ts"}
        has_signal = False

        for key, v in value.items():
            if str(key) in ignored_keys:
                continue
            if isinstance(v, bool):
                if v:
                    has_signal = True
                    break
                continue
            if isinstance(v, (int, float)):
                if float(v) != 0.0:
                    has_signal = True
                    break
                continue
            if isinstance(v, str):
                norm = v.strip().lower()
                if norm not in ("", "none", "null", "nan", "__null__"):
                    has_signal = True
                    break
                continue
            if isinstance(v, (list, dict)):
                if len(v) > 0:
                    has_signal = True
                    break
                continue
            if v is not None:
                has_signal = True
                break

        return not has_signal

    def _cache_set(self, key: str, value: Dict) -> None:
        """Store a cached association in runtime cache and, when enabled, on disk."""
        source, gene, disease_term = self._cache_parse_key(key)
        if not source:
            return
        ts = time.time()
        with self._cache_lock:
            self._association_runtime_cache[key] = value
            ttl = self._cache_ttl_for_source(source)
            if self.cache_enabled and ttl != 0 and self.cache_store is not None:
                self.cache_store.upsert_association(
                    source=source,
                    gene=gene,
                    disease_term=disease_term,
                    ts=ts,
                    value=value,
                )

    def _disease_cache_get(self, key: str) -> Optional[str]:
        with self._cache_lock:
            if key in self._disease_runtime_cache:
                value = self._disease_runtime_cache[key]
                return None if value == NULL_CACHE_VALUE else value
            if not self.cache_enabled or self.cache_store is None:
                return None
            row = self.cache_store.get_disease_id(key)
            if row is None:
                return None
            ts, value = row
            if not self._is_cache_valid(ts, "open_targets_disease"):
                self.cache_store.delete_disease_id(key)
                return None
            resolved = str(value) if value is not None else NULL_CACHE_VALUE
            self._disease_runtime_cache[key] = resolved
            return None if resolved == NULL_CACHE_VALUE else resolved

    def _disease_cache_set(self, key: str, value: Optional[str]) -> None:
        stored_value = value if value is not None else NULL_CACHE_VALUE
        with self._cache_lock:
            self._disease_runtime_cache[key] = stored_value
            ttl = self._cache_ttl_for_source("open_targets_disease")
            if self.cache_enabled and ttl != 0 and self.cache_store is not None:
                self.cache_store.upsert_disease_id(key, time.time(), stored_value)

    def _target_cache_get(self, gene_name: str) -> Optional[str]:
        key = str(gene_name).strip().upper()
        with self._cache_lock:
            if key in self._target_runtime_cache:
                value = self._target_runtime_cache[key]
                return None if value == NULL_CACHE_VALUE else value
            if not self.cache_enabled or self.cache_store is None:
                return None
            row = self.cache_store.get_target_id(key)
            if row is None:
                return None
            ts, value = row
            if not self._is_cache_valid(ts, "open_targets_target"):
                self.cache_store.delete_target_id(key)
                return None
            resolved = str(value) if value is not None else NULL_CACHE_VALUE
            self._target_runtime_cache[key] = resolved
            return None if resolved == NULL_CACHE_VALUE else resolved

    def _target_cache_set(self, gene_name: str, target_id: Optional[str]) -> None:
        key = str(gene_name).strip().upper()
        stored_value = target_id if target_id is not None else NULL_CACHE_VALUE
        with self._cache_lock:
            self._target_runtime_cache[key] = stored_value
            ttl = self._cache_ttl_for_source("open_targets_target")
            if self.cache_enabled and ttl != 0 and self.cache_store is not None:
                self.cache_store.upsert_target_id(key, time.time(), stored_value)

    def _is_cache_valid(self, ts: Optional[float], source: str) -> bool:
        if ts is None:
            return True
        ttl = self._cache_ttl_for_source(source)
        if ttl == 0:
            return False
        if ttl is None:
            return True
        return (time.time() - ts) <= (ttl * 86400)

    def _prune_disk_cache_locked(self) -> None:
        """Drop expired persisted cache entries. Caller must hold `_cache_lock`."""
        stale_associations = [
            key for key, entry in self._association_disk_cache.items()
            if not self._is_cache_valid(
                float(entry.get("ts")) if entry.get("ts") is not None else None,
                self._cache_parse_key(key)[0],
            )
        ]
        for key in stale_associations:
            self._association_disk_cache.pop(key, None)

        stale_diseases = [
            key for key, entry in self._disease_disk_cache.items()
            if not self._is_cache_valid(
                float(entry.get("ts")) if entry.get("ts") is not None else None,
                "open_targets_disease",
            )
        ]
        for key in stale_diseases:
            self._disease_disk_cache.pop(key, None)

        stale_targets = [
            key for key, entry in self._target_disk_cache.items()
            if not self._is_cache_valid(
                float(entry.get("ts")) if entry.get("ts") is not None else None,
                "open_targets_target",
            )
        ]
        for key in stale_targets:
            self._target_disk_cache.pop(key, None)
        if self.cache_store is not None and self.cache_enabled:
            self.cache_store.prune_expired(
                source_ttls_days={
                    "grok": self._cache_ttl_for_source("grok"),
                    "open_targets": self._cache_ttl_for_source("open_targets"),
                    "disgenet": self._cache_ttl_for_source("disgenet"),
                },
                disease_ttl_days=self._cache_ttl_for_source("open_targets_disease"),
                target_ttl_days=self._cache_ttl_for_source("open_targets_target"),
            )

    def _migrate_json_cache_to_sqlite_if_needed(self) -> None:
        if self.cache_store is None:
            return
        marker = self.cache_store.get_metadata("legacy_json_migrated")
        if not self.cache_file.exists():
            if marker != "1":
                self.cache_store.set_metadata("legacy_json_migrated", "1")
            return
        stat = self.cache_file.stat()
        fingerprint = f"{int(stat.st_mtime_ns)}:{int(stat.st_size)}"
        last_fp = self.cache_store.get_metadata("legacy_json_fingerprint")
        if marker == "1" and last_fp == fingerprint:
            return
        try:
            with open(self.cache_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            self.cache_store.import_legacy_payload(payload)
            migrated_name = self.cache_file.with_suffix(self.cache_file.suffix + ".migrated")
            try:
                if migrated_name.exists():
                    migrated_name = self.cache_file.with_suffix(
                        self.cache_file.suffix + f".migrated.{int(time.time())}"
                    )
                self.cache_file.rename(migrated_name)
                logger.info("Migrated legacy cache JSON -> SQLite and renamed to %s", migrated_name)
            except OSError:
                logger.info("Migrated legacy cache JSON -> SQLite (rename skipped)")
            self.cache_store.set_metadata("legacy_json_migrated", "1")
            self.cache_store.set_metadata("legacy_json_fingerprint", fingerprint)
        except Exception as exc:
            logger.warning("Failed JSON->SQLite cache migration: %s", exc)

    def _load_disk_cache(self) -> None:
        if not self.cache_enabled:
            return
        try:
            if self.cache_store is not None:
                self._migrate_json_cache_to_sqlite_if_needed()
                self.cache_store.prune_expired(
                    source_ttls_days={
                        "grok": self._cache_ttl_for_source("grok"),
                        "open_targets": self._cache_ttl_for_source("open_targets"),
                        "disgenet": self._cache_ttl_for_source("disgenet"),
                    },
                    disease_ttl_days=self._cache_ttl_for_source("open_targets_disease"),
                    target_ttl_days=self._cache_ttl_for_source("open_targets_target"),
                )
                logger.info(
                    "Enrichment cache DB ready at %s (%d associations)",
                    self.cache_db_path,
                    self.cache_store.association_count(),
                )
                return
            if not self.cache_file.exists():
                logger.info(f"Enrichment cache: none found (will use {self.cache_file} for new entries)")
                return
            with open(self.cache_file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            version = data.get("version", 1)
            associations = data.get("associations") or {}
            disease_ids = data.get("disease_ids") or {}
            target_ids = data.get("target_ids") or {}

            with self._cache_lock:
                self._association_disk_cache = {}
                self._disease_disk_cache = {}
                self._target_disk_cache = {}

                if isinstance(associations, list):
                    for row in associations:
                        source = str(row.get("source", "")).strip()
                        gene = str(row.get("gene", "")).strip().upper()
                        disease_term = str(row.get("disease_term", "")).strip()
                        if not source or not gene or not disease_term:
                            continue
                        try:
                            value = row.get("value")
                            if value is None:
                                value = json.loads(row.get("value_json", "{}"))
                        except (TypeError, ValueError):
                            continue
                        self._association_disk_cache[self._cache_key(source, gene, disease_term)] = {
                            "ts": float(row.get("ts")) if row.get("ts") is not None else time.time(),
                            "value": value,
                        }
                else:
                    for key, entry in (associations or {}).items():
                        if isinstance(entry, dict) and "value" in entry:
                            value = entry.get("value")
                            ts = entry.get("ts", time.time())
                        else:
                            value = entry
                            ts = time.time()
                        source, gene, disease_term = self._cache_parse_key(key)
                        if not source:
                            continue
                        self._association_disk_cache[self._cache_key(source, gene, disease_term)] = {
                            "ts": float(ts) if ts is not None else time.time(),
                            "value": value if isinstance(value, dict) else {},
                        }

                if isinstance(disease_ids, list):
                    for row in disease_ids:
                        term = str(row.get("disease_term", "")).strip()
                        if not term:
                            continue
                        self._disease_disk_cache[term] = {
                            "ts": float(row.get("ts")) if row.get("ts") is not None else time.time(),
                            "value": row.get("disease_id"),
                        }
                else:
                    for term, entry in (disease_ids or {}).items():
                        if isinstance(entry, dict) and "value" in entry:
                            value = entry.get("value")
                            ts = entry.get("ts", time.time())
                        else:
                            value = entry
                            ts = time.time()
                        self._disease_disk_cache[str(term)] = {
                            "ts": float(ts) if ts is not None else time.time(),
                            "value": value,
                        }

                if isinstance(target_ids, list):
                    for row in target_ids:
                        gene_name = str(row.get("gene_name", "")).strip().upper()
                        if not gene_name:
                            continue
                        self._target_disk_cache[gene_name] = {
                            "ts": float(row.get("ts")) if row.get("ts") is not None else time.time(),
                            "value": row.get("target_id"),
                        }
                else:
                    for gene_name, entry in (target_ids or {}).items():
                        if isinstance(entry, dict) and "value" in entry:
                            value = entry.get("value")
                            ts = entry.get("ts", time.time())
                        else:
                            value = entry
                            ts = time.time()
                        self._target_disk_cache[str(gene_name).upper()] = {
                            "ts": float(ts) if ts is not None else time.time(),
                            "value": value,
                        }

                self._prune_disk_cache_locked()
                self._cache_dirty = False
                n = len(self._association_disk_cache)
            logger.info(f"Enrichment cache: loaded {n} gene-disease associations from {self.cache_file}")
        except Exception as exc:
            logger.warning(f"Failed to load enrichment cache from {self.cache_file}: {exc}")

    def _save_disk_cache(self) -> None:
        if not self.cache_enabled:
            return
        try:
            if self.cache_store is not None:
                self.cache_store.prune_expired(
                    source_ttls_days={
                        "grok": self._cache_ttl_for_source("grok"),
                        "open_targets": self._cache_ttl_for_source("open_targets"),
                        "disgenet": self._cache_ttl_for_source("disgenet"),
                    },
                    disease_ttl_days=self._cache_ttl_for_source("open_targets_disease"),
                    target_ttl_days=self._cache_ttl_for_source("open_targets_target"),
                )
                return
            if not self._cache_dirty:
                return
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with self._cache_lock:
                associations = []
                for key, entry in self._association_disk_cache.items():
                    source, gene, disease_term = self._cache_parse_key(key)
                    associations.append({
                        "source": source,
                        "gene": gene,
                        "disease_term": disease_term,
                        "ts": entry.get("ts"),
                        "value_json": json.dumps(entry.get("value", {})),
                    })

                disease_ids = [
                    {
                        "disease_term": term,
                        "ts": entry.get("ts"),
                        "disease_id": entry.get("value"),
                    }
                    for term, entry in self._disease_disk_cache.items()
                ]
                target_ids = [
                    {
                        "gene_name": gene_name,
                        "ts": entry.get("ts"),
                        "target_id": entry.get("value"),
                    }
                    for gene_name, entry in self._target_disk_cache.items()
                ]
            payload = {
                "version": CACHE_VERSION,
                "saved_at": time.time(),
                "associations": associations,
                "disease_ids": disease_ids,
                "target_ids": target_ids,
            }
            with open(self.cache_file, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            self._cache_dirty = False
            logger.info(f"Enrichment cache: saved {len(associations)} associations to {self.cache_file}")
        except Exception as exc:
            logger.warning(f"Failed to save enrichment cache to {self.cache_file}: {exc}")

    def _open_targets_request(self, query: str, variables: Dict) -> Dict:
        """Execute a GraphQL request against Open Targets."""
        response = self._get_session().post(
            self.open_targets_api_url,
            json={"query": query, "variables": variables},
            timeout=30
        )
        response.raise_for_status()
        return response.json()

    def _resolve_open_targets_disease_id(self, disease_term: str) -> Optional[str]:
        """Resolve disease term to Open Targets disease ID."""
        cached = self._disease_cache_get(disease_term)
        if cached is not None:
            return cached

        query = """
        query DiseaseSearch($queryString: String!) {
          search(queryString: $queryString, entityNames: ["disease"]) {
            hits {
              id
              name
              entity
            }
          }
        }
        """
        try:
            result = self._open_targets_request(query, {"queryString": disease_term})
            hits = result.get("data", {}).get("search", {}).get("hits", [])
            disease_id = None
            term_lower = (disease_term or "").strip().lower()
            # Prefer a hit whose name matches the disease term (case-insensitive)
            for hit in hits:
                if str(hit.get("entity", "")).lower() != "disease":
                    continue
                name = (hit.get("name") or "").strip().lower()
                if term_lower and name and (term_lower in name or name in term_lower):
                    disease_id = hit.get("id")
                    logger.debug(f"Open Targets: matched disease '{disease_term}' to {disease_id} ({hit.get('name')})")
                    break
            # Fallback: first disease hit
            if not disease_id:
                for hit in hits:
                    if str(hit.get("entity", "")).lower() == "disease":
                        disease_id = hit.get("id")
                        break
            if not disease_id and hits:
                logger.warning(f"Open Targets: no disease entity in search hits for '{disease_term}' (first hit: {hits[0].get('entity')})")
            self._disease_cache_set(disease_term, disease_id)
            return disease_id
        except Exception as exc:
            logger.debug(f"Open Targets disease search failed: {exc}")
            return None

    def _resolve_open_targets_target_id(self, gene_name: str) -> Optional[str]:
        """Resolve gene symbol to Open Targets target ID."""
        cached = self._target_cache_get(gene_name)
        if cached is not None:
            return cached
        query = """
        query TargetSearch($queryString: String!) {
          search(queryString: $queryString, entityNames: ["target"]) {
            hits {
              id
              name
              entity
            }
          }
        }
        """
        try:
            result = self._open_targets_request(query, {"queryString": gene_name})
            hits = result.get("data", {}).get("search", {}).get("hits", [])
            target_id = None
            for hit in hits:
                if str(hit.get("entity", "")).lower() == "target":
                    target_id = hit.get("id")
                    break
            self._target_cache_set(gene_name, target_id)
            return target_id
        except Exception:
            raise

    def _fetch_open_targets_association(self, target_id: str, disease_id: str) -> Optional[Dict]:
        """Fetch Open Targets association score for target-disease pair."""
        # Open Targets Platform v4+ requires ensemblId (ENSG…), not target(id: …).
        # Bs restricts to the resolved disease id so we do not miss it when it ranks
        # below the default paginated window (scores are ordered globally per target).
        query = """
        query TargetDiseases($ensemblId: String!, $diseaseIds: [String!]!) {
          target(ensemblId: $ensemblId) {
            associatedDiseases(Bs: $diseaseIds, page: {index: 0, size: 5}) {
              rows {
                disease {
                  id
                  name
                }
                score
              }
            }
          }
        }
        """
        result = self._open_targets_request(
            query,
            {"ensemblId": target_id, "diseaseIds": [disease_id]},
        )
        rows = (
            result.get("data", {})
            .get("target", {})
            .get("associatedDiseases", {})
            .get("rows", [])
        )
        for row in rows:
            disease = row.get("disease", {}) or {}
            if disease.get("id") == disease_id:
                score = float(row.get("score", 0.0) or 0.0)
                evidence_level = (
                    "high" if score >= 0.6 else
                    "medium" if score >= 0.3 else
                    "low" if score > 0 else
                    "none"
                )
                return {
                    'associated': score > 0,
                    'association_type': 'database',
                    'evidence_level': evidence_level,
                    'description': disease.get("name"),
                    'publications': 0,
                    'functional_role': None,
                    'source': 'open_targets',
                    'score': score
                }
        return None

