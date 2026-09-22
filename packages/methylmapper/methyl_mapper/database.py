"""
Azure SQL Database connection and operations for MethylMapper.

Uses SQLModel for ORM operations, combining Pydantic validation and SQLAlchemy power.
"""

import logging
from typing import List, Optional
import pandas as pd
from sqlmodel import SQLModel, Session, create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

import numpy as np

from .config import AzureSQLConfig, StoredProcedureConfig
from .models import DMPStaging

logger = logging.getLogger(__name__)

# Default species_id for human (used when creating samples)
DEFAULT_SPECIES_ID = 9606


class AzureSQLConnection:
    """
    Manages Azure SQL Database connection and operations for DMP-to-gene mapping.
    
    Uses SQLModel for ORM operations and pandas for bulk data handling.
    """
    
    def __init__(self, config: AzureSQLConfig):
        """
        Initialize connection manager.
        
        Args:
            config: Azure SQL connection configuration
        """
        self.config = config
        self.engine: Optional[Engine] = None
    
    def connect(self) -> None:
        """
        Establish connection to Azure SQL Database.
        
        Raises:
            Exception: If connection fails
        """
        try:
            connection_string = self.config.get_connection_string()
            logger.info(f"Connecting to Azure SQL: {self.config.server}/{self.config.database}")
            
            # Create engine with connection pooling disabled for Azure SQL
            self.engine = create_engine(
                connection_string,
                poolclass=NullPool,  # Disable pooling for Azure SQL compatibility
                echo=False
            )
            
            # Test connection
            with Session(self.engine) as session:
                result = session.exec(text("SELECT @@VERSION")).one()
                logger.info(f"✅ Connected to Azure SQL Server")
                logger.debug(f"Server version: {result[:100]}...")
                
        except Exception as e:
            logger.error(f"Failed to connect to Azure SQL: {e}")
            raise
    
    def create_tables(self) -> None:
        """
        Create all SQLModel tables in the database if they don't exist.
        This is the SQLModel way - much simpler than manual table creation!
        """
        if self.engine is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        
        try:
            logger.info("Ensuring database tables exist...")
            SQLModel.metadata.create_all(self.engine)
            logger.info("✅ Database tables ready")
        except Exception as e:
            logger.error(f"Failed to create tables: {e}")
            raise
    
    def create_sample(self, species_id: int = DEFAULT_SPECIES_ID) -> int:
        """
        Create a new sample in dbo.Samples and return its ID.
        Use this when you need a new sample_id for uploading DMPs.

        Args:
            species_id: Species ID (default 9606 for human)

        Returns:
            New sample ID (IDENTITY value)
        """
        if self.engine is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        try:
            with Session(self.engine) as session:
                result = session.exec(
                    text("INSERT INTO dbo.Samples (species_id) OUTPUT INSERTED.ID VALUES (:sid)"),
                    params={"sid": species_id},
                )
                row = result.one()
                new_id = row[0] if hasattr(row, "__getitem__") else row
                session.commit()
                logger.info(f"Created sample_id={new_id} (species_id={species_id})")
                return int(new_id)
        except Exception as e:
            logger.error(f"Failed to create sample: {e}")
            raise

    def clear_sample_data(self, sample_id: int) -> int:
        """
        Clear existing DMPs for a sample ID from dbo.sample_dmps.

        Args:
            sample_id: Sample ID to clear

        Returns:
            Number of rows deleted
        """
        if self.engine is None:
            raise RuntimeError("Database not connected. Call connect() first.")

        try:
            with Session(self.engine) as session:
                result = session.exec(
                    text("DELETE FROM dbo.sample_dmps WHERE sample_id = :sid"),
                    params={"sid": sample_id},
                )
                session.commit()
                # SQL Server doesn't return rowcount from DELETE in the same way; re-query is not ideal
                # Use a separate count or rely on the fact we deleted
                logger.info(f"Cleared sample_dmps for sample_id={sample_id}")
                return 0  # Caller can re-check via get_sample_dmps if needed
        except Exception as e:
            logger.error(f"Failed to clear sample data: {e}")
            raise
    
    def upload_dmps(self, dmps_df: pd.DataFrame, sample_id: int) -> int:
        """
        Upload DMPs to dbo.sample_dmps for use by spMapDMP2Genes.

        Derives p_value (from CSV or q_value proxy), weight = -log10(p_value),
        and direction = sign(delta_mean) (1 or -1).

        Args:
            dmps_df: DataFrame with columns position, chromosome, context;
                     must have p_value (or q_value) and delta_mean for direction
            sample_id: Sample ID (must exist in dbo.Samples)

        Returns:
            Number of rows uploaded
        """
        if self.engine is None:
            raise RuntimeError("Database not connected. Call connect() first.")

        required = ['position', 'chromosome', 'context']
        missing = [c for c in required if c not in dmps_df.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        upload_df = dmps_df.copy()
        upload_df['sample_id'] = sample_id

        # p_value: use column if present, else use q_value as proxy (SP expects 0 < p_value < 1)
        if 'p_value' in upload_df.columns:
            pv = upload_df['p_value'].astype(float)
        elif 'q_value' in upload_df.columns:
            pv = upload_df['q_value'].astype(float)
        else:
            raise ValueError("DataFrame must contain p_value or q_value")
        pv = pv.clip(lower=1e-300, upper=1.0 - 1e-16)
        upload_df['p_value'] = pv

        # weight: -log10(p_value), clamp to avoid inf
        upload_df['weight'] = -np.log10(upload_df['p_value'].astype(float))
        upload_df['weight'] = upload_df['weight'].clip(lower=0.0, upper=300.0).fillna(1.0)

        # direction: sign(delta_mean), 1 or -1 (SP expects IN (-1, 1))
        if 'delta_mean' in upload_df.columns:
            d = np.sign(upload_df['delta_mean'].astype(float))
            upload_df['direction'] = d.replace(0, 1).astype(int)
        else:
            upload_df['direction'] = 1

        out_cols = ['sample_id', 'chromosome', 'context', 'position', 'p_value', 'weight', 'direction']
        upload_df = upload_df[out_cols]

        logger.info(f"Uploading {len(upload_df):,} DMPs to sample_dmps for sample_id={sample_id}...")
        upload_df.to_sql(
            name='sample_dmps',
            con=self.engine,
            if_exists='append',
            index=False,
            method='multi',
            chunksize=1000,
        )
        logger.info(f"✅ Uploaded {len(upload_df):,} DMPs to sample_dmps")
        return len(upload_df)
    
    def upload_dmps_orm(self, dmps: List[DMPStaging]) -> int:
        """
        Upload DMPs using SQLModel ORM (slower but validates through Pydantic).
        
        Use this when you need full validation. For bulk operations, use upload_dmps().
        
        Args:
            dmps: List of DMPStaging objects
            
        Returns:
            Number of rows uploaded
        """
        if self.engine is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        
        try:
            logger.info(f"Uploading {len(dmps):,} DMPs using ORM...")
            
            with Session(self.engine) as session:
                # Add all records
                for dmp in dmps:
                    session.add(dmp)
                
                # Commit in one transaction
                session.commit()
            
            logger.info(f"✅ Uploaded {len(dmps):,} DMPs via ORM")
            return len(dmps)
            
        except Exception as e:
            logger.error(f"Failed to upload DMPs via ORM: {e}")
            raise
    
    def get_sample_dmps(self, sample_id: int) -> pd.DataFrame:
        """
        Retrieve all DMPs for a sample from dbo.sample_dmps.

        Args:
            sample_id: Sample ID to retrieve

        Returns:
            DataFrame with columns sample_id, chromosome, context, position, p_value, weight, direction
        """
        if self.engine is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        try:
            with Session(self.engine) as session:
                result = session.exec(
                    text("SELECT sample_id, chromosome, context, position, p_value, weight, direction FROM dbo.sample_dmps WHERE sample_id = :sid"),
                    params={"sid": sample_id},
                )
                rows = result.fetchall()
                cols = result.keys()
                return pd.DataFrame(rows, columns=cols)
        except Exception as e:
            logger.error(f"Failed to retrieve sample DMPs: {e}")
            raise
    
    def execute_stored_procedure(
        self,
        sample_id: int,
        chromosome: str,
        context: str,
        sp_config: StoredProcedureConfig,
    ) -> pd.DataFrame:
        """
        Execute spMapDMP2Genes and return gene results from dbo.sample_genes.
        The SP inserts into sample_genes; this method fetches those rows.

        Args:
            sample_id: Sample ID to process
            chromosome: Chromosome identifier (e.g. '1', 'X')
            context: Methylation context (e.g., CG); used for logging only
            sp_config: Stored procedure parameters

        Returns:
            DataFrame with columns sample_id, chromosome, gene_id, gene_name, p_value, q_value, direction, strand
        """
        if self.engine is None:
            raise RuntimeError("Database not connected. Call connect() first.")

        try:
            logger.info(f"Executing spMapDMP2Genes for sample_id={sample_id}, chromosome={chromosome}...")
            with Session(self.engine) as session:
                session.exec(
                    text("""
                        EXEC spMapDMP2Genes
                            @sample_id = :sample_id,
                            @chromosome = :chromosome,
                            @paramID = :param_id
                    """),
                    params={
                        "sample_id": sample_id,
                        "chromosome": chromosome,
                        "param_id": sp_config.param_id,
                    },
                )
                session.commit()
            # SP inserts into sample_genes; fetch results
            with Session(self.engine) as session:
                result = session.exec(
                    text("""
                        SELECT sample_id, chromosome, gene_id, gene_name, p_value, q_value, direction, strand
                        FROM dbo.sample_genes
                        WHERE sample_id = :sid AND chromosome = :chr
                    """),
                    params={"sid": sample_id, "chr": chromosome},
                )
                rows = result.fetchall()
                cols = result.keys()
                df = pd.DataFrame(rows, columns=cols)
            logger.info(f"✅ Retrieved {len(df):,} gene mappings from sample_genes")
            return df
        except Exception as e:
            logger.error(f"Failed to execute stored procedure: {e}")
            raise
    
    def close(self) -> None:
        """Close database connection and dispose of engine."""
        if self.engine is not None:
            self.engine.dispose()
            logger.info("Database connection closed")
            self.engine = None
    
    def __enter__(self):
        """Context manager entry - automatically connects."""
        self.connect()
        self.create_tables()  # Ensure tables exist
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - automatically closes."""
        self.close()
