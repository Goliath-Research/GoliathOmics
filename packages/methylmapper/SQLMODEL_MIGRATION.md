# SQLModel Migration Guide

## Overview

The MethylMapper package has been successfully migrated from raw SQLAlchemy to **SQLModel**, combining the best of:
- **Pydantic**: Data validation and serialization
- **SQLAlchemy**: Powerful ORM and database operations

This provides a cleaner, more maintainable API with type safety and automatic validation.

## What Changed

### 1. New `models.py` Module

Created a dedicated models module with SQLModel-based table definitions:

```python
from sqlmodel import SQLModel, Field

class DMPStaging(SQLModel, table=True):
    """DMP staging table - both Pydantic model and SQLAlchemy ORM."""
    __tablename__ = "dmp_staging"
    
    id: Optional[int] = Field(default=None, primary_key=True)
    SampleID: int = Field(index=True, description="Sample ID")
    position: int = Field(description="Genomic position")
    chromosome: str = Field(max_length=10, description="Chromosome")
    context: str = Field(max_length=3, description="Methylation context")
    q_value: Optional[float] = Field(default=None, description="Q-value")
    delta_mean: Optional[float] = Field(default=None, description="Delta mean")
    overlap: Optional[float] = Field(default=None, description="Overlap")
    effect_size: Optional[float] = Field(default=None, description="Effect size")
```

**Benefits:**
- Single class serves as both Pydantic model (validation) and SQLAlchemy table (ORM)
- Automatic JSON serialization/deserialization
- Type hints for better IDE support
- Built-in validation on field assignment

### 2. Updated `database.py`

Switched from raw SQLAlchemy connections to SQLModel's `Session` API:

**Before (SQLAlchemy):**
```python
from sqlalchemy import create_engine, Table, Column, Integer, String
from sqlalchemy.orm import sessionmaker

engine = create_engine(connection_string)
Session = sessionmaker(bind=engine)
session = Session()
```

**After (SQLModel):**
```python
from sqlmodel import Session, create_engine, select

engine = create_engine(connection_string)
with Session(engine) as session:
    statement = select(DMPStaging).where(DMPStaging.SampleID == sample_id)
    results = session.exec(statement).all()
```

**Key improvements:**
- Cleaner, more Pythonic syntax
- Automatic table creation: `SQLModel.metadata.create_all(engine)`
- Type-safe queries with IDE autocomplete
- Context manager support for automatic cleanup

### 3. Enhanced `config.py`

Added validation to configuration models:

```python
class StoredProcedureConfig(BaseModel):
    upstream_size: int = Field(default=5000, ge=0)
    w_promoter: float = Field(default=2.0, gt=0.0)
    
    @field_validator('upstream_size', 'downstream_size', 'min_intron_size')
    @classmethod
    def validate_sizes(cls, v):
        if v > 1_000_000:
            raise ValueError(f"Region size {v} seems too large (max 1Mb)")
        return v
```

**Note:** Config models remain pure Pydantic (not SQLModel) since they're not database tables.

## New API Features

### 1. ORM-Style Operations

**Create and validate:**
```python
from methyl_mapper import DMPStaging

# Automatic validation on creation
dmp = DMPStaging(
    SampleID=1,
    position=12345678,
    chromosome="chr1",
    context="CG",
    q_value=0.001
)

# JSON serialization
json_str = dmp.model_dump_json()
```

### 2. Two Upload Methods

**Fast bulk upload (use for large datasets):**
```python
# Uses pandas.to_sql() - no per-row validation but much faster
conn.upload_dmps(dmps_df, sample_id=1)
```

**ORM upload with validation (use for small datasets or when validation is critical):**
```python
# Uses SQLModel ORM - validates every row
dmps = [DMPStaging(...) for row in data]
conn.upload_dmps_orm(dmps)
```

### 3. Type-Safe Queries

```python
from sqlmodel import select

with Session(engine) as session:
    # IDE provides autocomplete for DMPStaging fields!
    statement = select(DMPStaging).where(
        DMPStaging.SampleID == 1
    ).where(
        DMPStaging.chromosome == "chr1"
    )
    results = session.exec(statement).all()
```

### 4. Automatic Table Creation

No more manual table creation! SQLModel handles it:

```python
# Creates all tables defined in models.py if they don't exist
SQLModel.metadata.create_all(engine)
```

## Migration Checklist

If you have existing code using the old API:

- [x] Install SQLModel: `pip install sqlmodel`
- [x] Update imports: `from methyl_mapper import DMPStaging`
- [x] Use `AzureSQLConnection` context manager for automatic cleanup
- [x] Replace raw SQL with SQLModel queries where appropriate
- [x] Keep pandas-based bulk operations for large datasets
- [x] Add validation to configuration if needed

## Example Usage

### Complete Example

```python
from methyl_mapper import (
    AzureSQLConfig,
    AzureSQLConnection,
    DMPStaging,
    StoredProcedureConfig
)
import pandas as pd

# Configure connection
config = AzureSQLConfig(
    server="your-server.database.windows.net",
    database="your-database",
    username="your-username",
    password="your-password"
)

# Use context manager for automatic cleanup
with AzureSQLConnection(config) as conn:
    # Tables are automatically created
    
    # Clear existing data
    conn.clear_sample_data(sample_id=1)
    
    # Bulk upload from DataFrame (fast)
    dmps_df = pd.DataFrame({
        'position': [100, 200, 300],
        'chromosome': ['chr1', 'chr1', 'chr2'],
        'context': ['CG', 'CG', 'CHG'],
        'q_value': [0.001, 0.002, 0.003]
    })
    conn.upload_dmps(dmps_df, sample_id=1)
    
    # Or upload via ORM (with validation)
    dmps = [
        DMPStaging(SampleID=1, position=100, chromosome="chr1", context="CG"),
        DMPStaging(SampleID=1, position=200, chromosome="chr1", context="CG")
    ]
    conn.upload_dmps_orm(dmps)
    
    # Execute stored procedure
    sp_config = StoredProcedureConfig()
    results_df = conn.execute_stored_procedure(
        sample_id=1,
        chromosome="chr1",
        context="CG",
        sp_config=sp_config
    )
```

## Testing

Run the test suite to verify the migration:

```bash
cd /home/ubuntu/MethylPipeline
source setup_env.sh
python packages/methyl_mapper/methyl_mapper/examples/test_sqlmodel_migration.py
```

Expected output:
```
🎉 All tests passed! SQLModel migration successful!
Passed: 4/4
```

## Performance Considerations

1. **Bulk operations**: Use `upload_dmps()` (pandas) for large datasets
2. **Small datasets**: Use `upload_dmps_orm()` for full validation
3. **Connection pooling**: Disabled by default for Azure SQL compatibility
4. **Queries**: SQLModel queries are as fast as raw SQLAlchemy

## Benefits Summary

✅ **Type Safety**: Full IDE autocomplete and type checking  
✅ **Validation**: Automatic data validation via Pydantic  
✅ **Less Code**: Single class = model + table definition  
✅ **Better DX**: Cleaner, more Pythonic API  
✅ **Maintainability**: Easier to understand and modify  
✅ **Documentation**: Self-documenting with Field descriptions  

## Resources

- [SQLModel Documentation](https://sqlmodel.tiangolo.com/)
- [Pydantic Documentation](https://docs.pydantic.dev/)
- [SQLAlchemy Documentation](https://docs.sqlalchemy.org/)

## Next Steps

The migration is complete! You can now:

1. Use the new ORM-style API for cleaner code
2. Add more SQLModel tables as needed
3. Leverage Pydantic validation for data quality
4. Enjoy better IDE support and type safety

If you encounter any issues, check the test suite and examples for reference implementations.

