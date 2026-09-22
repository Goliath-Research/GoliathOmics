"""
SQLModel database models for MethylMapper.

These models serve dual purpose:
1. Pydantic models for validation and serialization
2. SQLAlchemy ORM models for database operations
"""

from typing import Optional
from sqlmodel import SQLModel, Field


class DMPStaging(SQLModel, table=True):
    """
    DMP (Differentially Methylated Position) staging table.
    
    This table temporarily holds DMP data before processing by spMapDMP2Genes.
    Serves as both Pydantic model and SQLAlchemy ORM model.
    """
    __tablename__ = "dmp_staging"
    
    # Primary key (auto-generated)
    id: Optional[int] = Field(default=None, primary_key=True)
    
    # Sample identifier (indexed for fast queries)
    SampleID: int = Field(index=True, description="Sample ID for tracking and grouping")
    
    # Genomic position
    position: int = Field(description="Genomic position (base pair coordinate)")
    chromosome: str = Field(max_length=10, description="Chromosome identifier (e.g., 'chr1', 'X')")
    context: str = Field(max_length=3, description="Methylation context (e.g., 'CG', 'CHG', 'CHH')")
    
    # Statistical values
    q_value: Optional[float] = Field(default=None, description="FDR-corrected q-value")
    delta_mean: Optional[float] = Field(default=None, description="Difference in mean methylation")
    overlap: Optional[float] = Field(default=None, description="Distribution overlap metric")
    effect_size: Optional[float] = Field(default=None, description="Effect size measure")
    
    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "SampleID": 1,
                "position": 12345678,
                "chromosome": "chr1",
                "context": "CG",
                "q_value": 0.001,
                "delta_mean": 0.25,
                "overlap": 0.15,
                "effect_size": 0.8
            }
        }


class SampleDMP(SQLModel):
    """
    DMP row for dbo.sample_dmps (STRING-DB).
    Used by spMapDMP2Genes. Not used for table creation (table exists in DB).
    """
    sample_id: int
    chromosome: str = Field(max_length=10)
    context: str = Field(max_length=3)
    position: int
    p_value: float = Field(gt=0, lt=1)
    weight: float = Field(gt=0)
    direction: int = Field(description="1 or -1 (sign of methylation change)")


class GeneMappingResult(SQLModel):
    """
    Gene mapping result from spMapDMP2Genes stored procedure.
    
    This is a Pydantic-only model (not a table) for API responses and validation.
    """
    gene_name: str = Field(description="Gene name/symbol")
    gene_id: Optional[str] = Field(default=None, description="Gene ID (e.g., Ensembl)")
    region: str = Field(description="Genomic region type (promoter, exon, intron, etc.)")
    weight: float = Field(description="Weighted score for this mapping")
    dmp_count: int = Field(description="Number of DMPs mapped to this gene")
    
    class Config:
        """Pydantic configuration."""
        json_schema_extra = {
            "example": {
                "gene_name": "TP53",
                "gene_id": "ENSG00000141510",
                "region": "promoter",
                "weight": 2.5,
                "dmp_count": 3
            }
        }

