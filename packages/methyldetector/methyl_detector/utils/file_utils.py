"""File utility functions for MethylDetector."""

import json
import logging
from pathlib import Path
from typing import Dict, Union

from ..models.config import MethylDetectorConfig

logger = logging.getLogger(__name__)


def create_output_directory(output_dir: Union[str, Path]) -> Path:
    """
    Create output directory if it doesn't exist.
    
    Args:
        output_dir: Output directory path
        
    Returns:
        Path object for the output directory
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Created output directory: {output_path}")
    return output_path


def load_config_from_json(json_path: Union[str, Path]) -> MethylDetectorConfig:
    """
    Load MethylDetector configuration from JSON file.

    The configuration file must contain valid MethylDetectorConfig parameters.
    Warns about unused parameters that are not defined in the model.

    Args:
        json_path: Path to JSON configuration file

    Returns:
        MethylDetectorConfig object
    """
    json_path = Path(json_path)

    if not json_path.exists():
        raise FileNotFoundError(f"Configuration file does not exist: {json_path}")

    with open(json_path, 'r') as f:
        config_data = json.load(f)

    # Get all valid field names from the Pydantic model
    valid_fields = set(MethylDetectorConfig.model_fields.keys())

    # Check for unused parameters
    unused_params = []
    for key in config_data.keys():
        if key not in valid_fields:
            unused_params.append(key)

    if unused_params:
        logger.warning(f"Found {len(unused_params)} unused parameter(s) in config file: {', '.join(unused_params)}")
        logger.warning("These parameters will be ignored. Consider removing them to simplify the config.")

    # The unified config will validate the mode and required fields
    return MethylDetectorConfig(**config_data)


def get_chromosome_context_from_filename(filepath: Union[str, Path]) -> Dict[str, str]:
    """
    Extract chromosome and context information from filename like '1-CG.h5'.
    
    Args:
        filepath: Path to HDF5 file
        
    Returns:
        Dictionary with 'chromosome' and 'context' keys
    """
    filename = Path(filepath).stem  # Remove extension
    
    if '-' in filename:
        parts = filename.split('-')
        if len(parts) == 2:
            return {
                'chromosome': parts[0],
                'context': parts[1]
            }
    
    # Fallback
    return {
        'chromosome': 'unknown',
        'context': 'unknown'
    }
