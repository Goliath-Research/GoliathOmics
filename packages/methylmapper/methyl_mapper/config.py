"""
Configuration models for MethylMapper.

Note: These are pure Pydantic models (not database tables).
For database models, see models.py which uses SQLModel.
"""

import os
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BiologyWeightConfig(BaseModel):
    """Per (feature × hyper/hypo) weights for biology-aware DMP aggregation in MethylMapper."""

    model_config = ConfigDict(extra="ignore")

    promoter_hyper: float = Field(default=2.0, gt=0.0)
    promoter_hypo: float = Field(default=1.0, gt=0.0)
    exon_hyper: float = Field(default=1.5, gt=0.0)
    exon_hypo: float = Field(default=1.0, gt=0.0)
    intron_hyper: float = Field(default=0.5, gt=0.0)
    intron_hypo: float = Field(default=0.7, gt=0.0)
    gene_body_hyper: float = Field(default=1.0, gt=0.0)
    gene_body_hypo: float = Field(default=1.0, gt=0.0)
    terminator_hyper: float = Field(default=0.5, gt=0.0)
    terminator_hypo: float = Field(default=0.5, gt=0.0)

    def weight_for(self, feature: str, *, hyper: bool) -> float:
        key = f"{feature}_{'hyper' if hyper else 'hypo'}"
        return float(getattr(self, key, 1.0))

    @classmethod
    def from_mapping(cls, data: Optional[Dict[str, float]]) -> "BiologyWeightConfig":
        if not data:
            return cls()
        return cls(**{k: v for k, v in data.items() if k in cls.model_fields})


class AzureSQLConfig(BaseModel):
    """Azure SQL Database connection configuration."""
    
    server: str = Field(
        default="em-maindb.database.windows.net",
        description="Azure SQL Server hostname (e.g., your-server.database.windows.net)"
    )
    database: str = Field(
        default="STRING-DB",
        description="Database name"
    )
    username: str = Field(
        default="dba",
        description="Database username"
    )
    password: str = Field(
        default="",
        description="Database password (prefer secure storage; JSON discouraged)"
    )
    azure_key_vault_url: Optional[str] = Field(
        default=None,
        description="Azure Key Vault URL for resolving/storing the SQL password (or AZURE_KEY_VAULT_URL env)",
    )
    driver: str = Field(
        default="ODBC Driver 18 for SQL Server",
        description="ODBC driver name"
    )
    port: int = Field(
        default=1433,
        description="Database port"
    )
    
    @model_validator(mode='after')
    def resolve_password(self):
        """Resolve password: explicit (e.g. JSON) → encrypted file → Key Vault → env (same order as API keys)."""
        try:
            from .secure_credentials import SecureCredentialManager
            vault_url = self.azure_key_vault_url or os.environ.get("AZURE_KEY_VAULT_URL")
            explicit = (self.password or "").strip() or None
            credential_manager = SecureCredentialManager(
                credential_name="azure_sql_password",
                env_var_name="AZURE_SQL_PASSWORD",
                azure_key_vault_url=vault_url,
            )
            self.password = credential_manager.get_credential(explicit_key=explicit) or ""
        except Exception:
            self.password = (self.password or "").strip()
        return self
    
    def get_connection_string(self) -> str:
        """Build SQLAlchemy connection string for Azure SQL."""
        import urllib.parse
        
        # Build ODBC connection string
        odbc_str = (
            f"DRIVER={{{self.driver}}};"
            f"SERVER={self.server},{self.port};"
            f"DATABASE={self.database};"
            f"UID={self.username};"
            f"PWD={self.password};"
            "Encrypt=yes;"
            "TrustServerCertificate=no;"
            "Connection Timeout=30;"
        )
        
        # URL-encode for SQLAlchemy
        params = urllib.parse.quote_plus(odbc_str)
        return f"mssql+pyodbc:///?odbc_connect={params}"


class StoredProcedureConfig(BaseModel):
    """Configuration for spMapDMP2Genes stored procedure parameters."""
    param_id: int = Field(
        default=1,
        ge=1,
        description="dbo.params ID used by spMapDMP2Genes"
    )
    upstream_size: int = Field(
        default=5000,
        ge=0,
        description="Upstream region size for promoter mapping (bp)"
    )
    downstream_size: int = Field(
        default=2000,
        ge=0,
        description="Downstream region size for terminator mapping (bp)"
    )
    min_intron_size: int = Field(
        default=0,
        ge=0,
        description="Minimum intron size to consider (bp)"
    )
    w_promoter: float = Field(
        default=2.0,
        gt=0.0,
        description="Weight for promoter region DMPs"
    )
    w_terminator: float = Field(
        default=0.5,
        gt=0.0,
        description="Weight for terminator region DMPs"
    )
    w_gene_body: float = Field(
        default=1.0,
        gt=0.0,
        description="Weight for gene body DMPs"
    )
    w_exon: float = Field(
        default=1.5,
        gt=0.0,
        description="Weight for exon DMPs"
    )
    w_intron: float = Field(
        default=0.7,
        gt=0.0,
        description="Weight for intron DMPs"
    )
    w_unknown: float = Field(
        default=1.0,
        gt=0.0,
        description="Weight for unknown region DMPs"
    )
    
    @field_validator('upstream_size', 'downstream_size', 'min_intron_size')
    @classmethod
    def validate_sizes(cls, v):
        """Validate region sizes are reasonable."""
        if v > 1_000_000:  # 1 Mb
            raise ValueError(f"Region size {v} seems too large (max 1Mb)")
        return v


class MapperStepConfig(BaseModel):
    """
    Pydantic model for step_config.mapper in the pipeline project JSON (and --config).
    All fields optional; used to validate and access mapper options as config.grok_max_workers etc.
    """

    model_config = ConfigDict(extra="ignore")

    csv_pattern: Optional[str] = None
    csv_filename_pattern: Optional[str] = None
    output_dir: Optional[str] = None
    gtf: Optional[str] = None
    disease_term: Optional[str] = None
    enrich_disease: Optional[bool] = None
    enrich_source: Optional[str] = None
    plant_traits_path: Optional[str] = Field(
        default=None,
        description=(
            "Path to offline gene↔trait TSV/CSV for enrich_source=plant_traits "
            "(columns: gene, disease_term/trait_term, optional score/evidence). "
            "Operator-set per profile/site/instance; required when plant_traits is selected. "
            "Do not use Open Targets for plant packs."
        ),
    )
    enrich_profile: Optional[str] = None
    grok_api_key: Optional[str] = None
    disgenet_api_key: Optional[str] = None
    persist_secrets: Optional[bool] = None
    grok_max_workers: Optional[int] = None
    grok_batch_size: Optional[int] = None
    grok_rate_limit_delay: Optional[float] = None
    grok_max_retries: Optional[int] = None
    grok_429_cooldown: Optional[float] = None
    grok_batch_api: Optional[bool] = None
    grok_batch_poll_interval: Optional[float] = None
    grok_batch_submit_chunk_size: Optional[int] = None
    grok_cache_ttl_days: Optional[int] = None
    cache_dir: Optional[str] = None
    cache_backend: Optional[str] = None
    cache_db_path: Optional[str] = None
    cache_ttl_days: Optional[int] = None
    methyl_mapper_home: Optional[str] = None
    azure_key_vault_url: Optional[str] = None
    encrypted_file_path: Optional[str] = None
    optimize_dmps: Optional[bool] = None
    extend_after_stable: Optional[bool] = None
    feature_types: Optional[list] = None
    auxiliary_bed_paths: Optional[List[str]] = None
    run_bedtools_closest: Optional[bool] = None
    closest_gene_bed: Optional[str] = None
    # SP-equivalent / bedtools parity (step_config.mapper in project JSON)
    use_sp_regions: Optional[bool] = None
    storey_lambda: Optional[float] = None
    upstream_size: Optional[int] = None
    downstream_size: Optional[int] = None
    min_intron_size: Optional[int] = None
    w_promoter: Optional[float] = None
    w_terminator: Optional[float] = None
    w_gene_body: Optional[float] = None
    w_exon: Optional[float] = None
    w_intron: Optional[float] = None
    w_unknown: Optional[float] = None
    biology_weights: Optional[BiologyWeightConfig] = None


class MethylMapperConfig(BaseModel):
    """Complete configuration for MethylMapper."""

    database: AzureSQLConfig = Field(
        ...,
        description="Azure SQL Database connection configuration"
    )
    stored_procedure: StoredProcedureConfig = Field(
        default_factory=StoredProcedureConfig,
        description="Stored procedure parameters"
    )
    input_csv: Optional[str] = Field(
        default=None,
        description="Input DMP CSV file path"
    )
    output_csv: Optional[str] = Field(
        default="mapped_genes.csv",
        description="Output CSV file path for gene mapping results"
    )
    output_json: Optional[str] = Field(
        default="mapped_genes.json",
        description="Output JSON file path for gene names list"
    )
    sample_id: Optional[int] = Field(
        default=None,
        description="Sample ID for database tracking"
    )
    chromosome: Optional[str] = Field(
        default=None,
        description="Chromosome (override from CSV if specified)"
    )
    context: Optional[str] = Field(
        default=None,
        description="Context (override from CSV if specified)"
    )

