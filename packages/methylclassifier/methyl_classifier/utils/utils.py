"""
Utility functions for MethylClassifier
"""

import logging
import sys
from pathlib import Path
from typing import Optional


def setup_logging(level: str = "INFO", log_file: Optional[Path] = None):
    """
    Setup logging configuration for MethylClassifier.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional log file path
    """
    # Convert string level to logging level
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f'Invalid log level: {level}')

    # Configure logging
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    formatter = logging.Formatter(log_format)

    # Setup root logger
    logger = logging.getLogger()
    logger.setLevel(numeric_level)

    # Remove existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler if specified
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)


def extract_chrom_context_from_classifier(classifier_path: Path) -> tuple[str, str]:
    """
    Extract chromosome and context from classifier filename.

    Args:
        classifier_path: Path to classifier file

    Returns:
        Tuple of (chromosome, context)

    Raises:
        ValueError: If chromosome and context cannot be extracted
    """
    filename = classifier_path.stem  # Remove .pkl extension
    parts = filename.split('-')

    if len(parts) >= 3 and parts[-2].isdigit():
        # Format: something-chromosome-context-classifier.pkl
        chrom = parts[-2]
        context = parts[-1]
        return chrom, context

    # Fallback: try to extract from path
    path_parts = classifier_path.parts
    for part in reversed(path_parts):
        if '-' in part and (part.endswith('-CG') or part.endswith('-CHG') or part.endswith('-CHH')):
            parts = part.split('-')
            if len(parts) >= 2:
                chrom = parts[-2]
                context = parts[-1]
                return chrom, context

    raise ValueError(f"Could not extract chromosome and context from {classifier_path}")


def format_sample_name(h5_file: Path) -> str:
    """
    Format sample name from H5 file path.
    Uses parent directory name as sample identifier.

    Args:
        h5_file: Path to H5 file

    Returns:
        Formatted sample name
    """
    return h5_file.parent.name
