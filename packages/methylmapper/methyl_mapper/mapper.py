"""
Core DMP-to-gene mapping functionality for MethylMapper
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
import pandas as pd

from .config import MethylMapperConfig, StoredProcedureConfig
from .database import AzureSQLConnection

logger = logging.getLogger(__name__)


class DMPMapper:
    """
    Main class for mapping DMPs to genes using Azure SQL Database.
    
    Workflow:
    1. Load DMP CSV file
    2. Upload to Azure SQL staging table
    3. Execute stored procedure for gene mapping
    4. Save results to CSV and JSON
    """
    
    def __init__(self, config: MethylMapperConfig):
        """
        Initialize DMPMapper.
        
        Args:
            config: MethylMapper configuration
        """
        self.config = config
        self.dmps_df: Optional[pd.DataFrame] = None
        self.genes_df: Optional[pd.DataFrame] = None
    
    def load_dmps(self, csv_path: Path) -> pd.DataFrame:
        """
        Load DMP CSV file.
        
        Args:
            csv_path: Path to DMP CSV file
            
        Returns:
            DataFrame with DMP data
            
        Raises:
            FileNotFoundError: If CSV file doesn't exist
            ValueError: If CSV is missing required columns
        """
        csv_path = Path(csv_path)
        
        if not csv_path.exists():
            raise FileNotFoundError(f"DMP CSV file not found: {csv_path}")
        
        logger.info(f"Loading DMPs from {csv_path}...")
        
        try:
            dmps_df = pd.read_csv(csv_path)
            
            # Required for upload to sample_dmps: position, chromosome, context, and (p_value or q_value), delta_mean for direction
            required_cols = ['position', 'chromosome', 'context']
            missing_cols = [col for col in required_cols if col not in dmps_df.columns]
            if missing_cols:
                raise ValueError(f"Missing required columns: {missing_cols}")
            if 'p_value' not in dmps_df.columns and 'q_value' not in dmps_df.columns:
                raise ValueError("DataFrame must contain p_value or q_value for sample_dmps upload")
            if 'delta_mean' not in dmps_df.columns:
                dmps_df['delta_mean'] = 0.0  # direction will default to 1 in database layer
            # Optional columns (fill if missing)
            for col in ['overlap', 'effect_size']:
                if col not in dmps_df.columns:
                    dmps_df[col] = 0.0 if col == 'overlap' else None
            
            logger.info(f"✅ Loaded {len(dmps_df):,} DMPs from CSV")
            logger.info(f"Columns: {', '.join(dmps_df.columns)}")
            
            # Show summary
            if not dmps_df.empty:
                logger.info(f"Chromosome(s): {dmps_df['chromosome'].unique()}")
                logger.info(f"Context(s): {dmps_df['context'].unique()}")
                logger.info(f"Effect size range: {dmps_df['effect_size'].min():.2f} - {dmps_df['effect_size'].max():.2f}")
            
            self.dmps_df = dmps_df
            return dmps_df
            
        except Exception as e:
            logger.error(f"Failed to load DMP CSV: {e}")
            raise
    
    def map_dmps_to_genes(
        self,
        sample_id: int,
        chromosome: Optional[str] = None,
        context: Optional[str] = None,
        sp_config: Optional[StoredProcedureConfig] = None
    ) -> pd.DataFrame:
        """
        Map DMPs to genes using Azure SQL stored procedure.
        
        Args:
            sample_id: Sample ID for database tracking
            chromosome: Chromosome (uses first from DataFrame if not specified)
            context: Context (uses first from DataFrame if not specified)
            sp_config: Stored procedure configuration (uses config default if not specified)
            
        Returns:
            DataFrame with gene mapping results
        """
        if self.dmps_df is None or self.dmps_df.empty:
            raise ValueError("No DMPs loaded. Call load_dmps() first.")
        
        # Determine chromosome and context
        if chromosome is None:
            chromosome = self.dmps_df['chromosome'].iloc[0]
        if context is None:
            context = self.dmps_df['context'].iloc[0]
        
        if sp_config is None:
            sp_config = self.config.stored_procedure
        
        logger.info(f"Mapping DMPs to genes for SampleID={sample_id}, {chromosome}-{context}")
        
        try:
            # Connect to database
            with AzureSQLConnection(self.config.database) as db:
                # Clear existing DMPs for this sample (sample_dmps)
                db.clear_sample_data(sample_id)
                
                # Upload DMPs
                db.upload_dmps(self.dmps_df, sample_id)
                
                # Execute stored procedure
                genes_df = db.execute_stored_procedure(
                    sample_id=sample_id,
                    chromosome=chromosome,
                    context=context,
                    sp_config=sp_config
                )
                
                self.genes_df = genes_df
                return genes_df
                
        except Exception as e:
            logger.error(f"Failed to map DMPs to genes: {e}")
            raise
    
    def save_results(
        self,
        output_csv: Path,
        output_json: Path,
        gene_column: str = 'gene_name'
    ) -> Dict[str, Any]:
        """
        Save gene mapping results to CSV and JSON.
        
        Args:
            output_csv: Path for output CSV file (full results)
            output_json: Path for output JSON file (gene names only, ordered)
            gene_column: Column name containing gene names
            
        Returns:
            Dictionary with save statistics
        """
        if self.genes_df is None or self.genes_df.empty:
            logger.warning("No gene mapping results to save")
            return {'csv_rows': 0, 'json_genes': 0}
        
        output_csv = Path(output_csv)
        output_json = Path(output_json)
        
        # Create output directories if needed
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        output_json.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            # Save full results to CSV
            self.genes_df.to_csv(output_csv, index=False)
            logger.info(f"✅ Saved full results to {output_csv} ({len(self.genes_df):,} rows)")
            
            # Extract gene names for JSON (ordered by importance if available)
            if gene_column in self.genes_df.columns:
                # Sort by importance/score if available
                importance_cols = ['effect_size', 'importance', 'score', 'weight']
                sort_col = None
                for col in importance_cols:
                    if col in self.genes_df.columns:
                        sort_col = col
                        break
                
                if sort_col:
                    genes_sorted = self.genes_df.sort_values(sort_col, ascending=False)
                    logger.info(f"Gene names sorted by {sort_col}")
                else:
                    genes_sorted = self.genes_df
                
                gene_names = genes_sorted[gene_column].dropna().unique().tolist()
            else:
                # Try to find any column with 'gene' in the name
                gene_cols = [col for col in self.genes_df.columns if 'gene' in col.lower()]
                if gene_cols:
                    gene_column = gene_cols[0]
                    logger.warning(f"Using column '{gene_column}' for gene names")
                    gene_names = self.genes_df[gene_column].dropna().unique().tolist()
                else:
                    logger.error(f"No gene column found in results. Available columns: {self.genes_df.columns.tolist()}")
                    gene_names = []
            
            # Save gene names to JSON
            with open(output_json, 'w') as f:
                json.dump(gene_names, f, indent=2)
            
            logger.info(f"✅ Saved gene names to {output_json} ({len(gene_names)} unique genes)")
            
            return {
                'csv_rows': len(self.genes_df),
                'json_genes': len(gene_names),
                'csv_path': str(output_csv),
                'json_path': str(output_json)
            }
            
        except Exception as e:
            logger.error(f"Failed to save results: {e}")
            raise
    
    def run(
        self,
        input_csv: Path,
        output_csv: Path,
        output_json: Path,
        sample_id: int,
        chromosome: Optional[str] = None,
        context: Optional[str] = None,
        sp_config: Optional[StoredProcedureConfig] = None
    ) -> Dict[str, Any]:
        """
        Run complete DMP-to-gene mapping pipeline.
        
        Args:
            input_csv: Input DMP CSV file
            output_csv: Output CSV file for full results
            output_json: Output JSON file for gene names
            sample_id: Sample ID for database
            chromosome: Chromosome (optional, auto-detected from CSV)
            context: Context (optional, auto-detected from CSV)
            sp_config: Stored procedure configuration (optional)
            
        Returns:
            Dictionary with pipeline results
        """
        logger.info("=" * 70)
        logger.info("MethylMapper - DMP to Gene Mapping Pipeline")
        logger.info("=" * 70)
        
        try:
            # Step 1: Load DMPs
            logger.info("\n📂 Step 1: Loading DMPs...")
            self.load_dmps(input_csv)
            
            # Step 2: Map to genes
            logger.info("\n🔗 Step 2: Mapping DMPs to genes...")
            genes_df = self.map_dmps_to_genes(
                sample_id=sample_id,
                chromosome=chromosome,
                context=context,
                sp_config=sp_config
            )
            
            # Step 3: Save results
            logger.info("\n💾 Step 3: Saving results...")
            save_stats = self.save_results(output_csv, output_json)
            
            logger.info("\n" + "=" * 70)
            logger.info("✅ MethylMapper Pipeline Complete!")
            logger.info("=" * 70)
            logger.info(f"Input DMPs: {len(self.dmps_df):,}")
            logger.info(f"Mapped genes: {save_stats['json_genes']}")
            logger.info(f"Full results: {save_stats['csv_path']}")
            logger.info(f"Gene list: {save_stats['json_path']}")
            logger.info("=" * 70)
            
            result = {
                'success': True,
                'input_dmps': len(self.dmps_df),
                'output_genes': save_stats['json_genes'],
                'output_rows': save_stats['csv_rows'],
                **save_stats
            }
            try:
                from methyl_domain.action_result import atomic_write_json, manifest_path_for

                out_dir = Path(output_csv).parent
                manifest = manifest_path_for(out_dir, "pipeline.mapper", Path(output_csv).stem)
                atomic_write_json(
                    manifest,
                    {
                        "schema_version": "1.0",
                        "status": "ok",
                        "action_name": "pipeline.mapper",
                        "output_dir": str(out_dir),
                        "n_input_dmps": result["input_dmps"],
                        "n_output_genes": result["output_genes"],
                        "output_csv": save_stats.get("csv_path") or str(output_csv),
                        "output_json": save_stats.get("json_path") or str(output_json),
                        "result_code": 0,
                    },
                )
            except Exception:
                logger.debug("Could not write mapper action manifest", exc_info=True)
            return result
        except Exception as e:
            logger.error(f"\n❌ Pipeline failed: {e}")
            raise

