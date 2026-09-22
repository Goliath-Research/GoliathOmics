"""
MethylMapper maps detector DMP exports onto genes and genomic features.

The supported workflow is the project-aware, bedtools-based mapper used by the
main MethylPipeline run path. Legacy Azure SQL helpers are kept for backward
compatibility but are not the primary interface.
"""

from .config import (
    MethylMapperConfig,
    MapperStepConfig,
    AzureSQLConfig,
    StoredProcedureConfig,
    BiologyWeightConfig,
)
from .database import AzureSQLConnection
from .models import DMPStaging, GeneMappingResult, SampleDMP
from .mapper import DMPMapper
from .bedtools_mapper import BedtoolsMapper
from .cache_db import SQLiteCacheStore
from .gene_disease_enricher import GeneDiseaseEnricher
from .secure_credentials import SecureCredentialManager, persist_secret_if_changed

__version__ = "0.1.0"
__all__ = [
    # Configuration
    "MethylMapperConfig",
    "MapperStepConfig",
    "BiologyWeightConfig",
    "AzureSQLConfig",
    "StoredProcedureConfig",
    # Database
    "AzureSQLConnection",
    # Models
    "DMPStaging",
    "SampleDMP",
    "GeneMappingResult",
    # High-level API
    "DMPMapper",
    "BedtoolsMapper",
    "SQLiteCacheStore",
    "GeneDiseaseEnricher",
    "SecureCredentialManager",
    "persist_secret_if_changed",
]

