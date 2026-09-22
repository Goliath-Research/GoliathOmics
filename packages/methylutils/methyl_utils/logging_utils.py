"""
Shared logging utilities for consistent logging across applications.

This module provides standardized logging configuration and utilities
that can be used across MethylCentroid, MethylDetector, MethylCluster, and other applications.
"""

import logging
import sys
from typing import Optional, Union
from pathlib import Path

def setup_logging(
    verbose: bool = False,
    log_file: Optional[Path] = None,
    log_level: Optional[str] = None,
    console_level: Optional[str] = None,
    file_level: Optional[str] = None
) -> None:
    """
    Configure logging for the application.
    
    Args:
        verbose: If True, use DEBUG level and detailed format
        log_file: Optional file path to write logs to
        log_level: Optional log level override ('DEBUG', 'INFO', 'WARNING', 'ERROR')
        console_level: Optional console-specific log level (if None, uses log_level)
        file_level: Optional file-specific log level (if None, uses log_level)
    """
    # Determine log level
    if log_level:
        level = getattr(logging, log_level.upper(), logging.INFO)
    else:
        level = logging.DEBUG if verbose else logging.INFO
    
    # Determine console and file levels
    console_log_level = getattr(logging, console_level.upper(), level) if console_level else level
    file_log_level = getattr(logging, file_level.upper(), level) if file_level else level
    
    # Determine format
    if verbose:
        format_str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    else:
        format_str = '%(levelname)s: %(message)s'
    
    # Create formatters
    console_formatter = logging.Formatter(format_str)
    file_formatter = logging.Formatter(format_str)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(min(console_log_level, file_log_level))
    
    # Clear existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Add console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_log_level)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)
    
    # Add file handler if specified
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(file_log_level)
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)
    
    # Configure all existing loggers
    for name in logging.root.manager.loggerDict:
        logger = logging.getLogger(name)
        logger.setLevel(min(console_log_level, file_log_level))
        # Remove existing handlers to avoid duplicates
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        logger.propagate = True

def setup_module_logging(module_name: str, verbose: bool = False) -> logging.Logger:
    """
    Setup module-level logging.
    
    Args:
        module_name: Name of the module (usually __name__)
        verbose: If True, use DEBUG level
        
    Returns:
        Configured logger for the module
    """
    logger = logging.getLogger(module_name)
    level = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(level)
    return logger

def get_logger(name: str, verbose: bool = False) -> logging.Logger:
    """
    Get a logger with consistent configuration.
    
    Args:
        name: Logger name (usually __name__)
        verbose: If True, use DEBUG level
        
    Returns:
        Configured logger
    """
    logger = logging.getLogger(name)
    level = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(level)
    return logger

def configure_gpu_logging(verbose: bool = False) -> None:
    """
    Configure logging specifically for GPU-related operations.
    
    Args:
        verbose: If True, enable detailed GPU logging
    """
    # Set up GPU-specific loggers
    gpu_loggers = [
        'shared_gpu_utils.gpu_detection',
        'cupy',
        'cupyx',
        'nvidia_ml_py'
    ]
    
    level = logging.DEBUG if verbose else logging.INFO
    
    for logger_name in gpu_loggers:
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        
        # Add a filter to reduce noise from CuPy
        if logger_name in ['cupy', 'cupyx'] and not verbose:
            logger.setLevel(logging.WARNING)

def log_gpu_info(logger: logging.Logger) -> None:
    """
    Log current GPU information to the specified logger.
    
    Args:
        logger: Logger instance to use
    """
    try:
        from .gpu_detection import get_gpu_state
        state = get_gpu_state()
        
        if state['available']:
            logger.info(f"GPU: {state['gpu_name']} ({state['memory_gb']:.1f}GB)")
            logger.info(f"CUDA: {state['cuda_version']}, Compute: {state['compute_capability']}")
        else:
            logger.info("GPU acceleration not available")
            if state['error_message']:
                logger.debug(f"GPU unavailable: {state['error_message']}")
    except Exception as e:
        logger.warning(f"Failed to log GPU info: {e}")

def create_log_file_path(base_dir: Path, app_name: str, timestamp: Optional[str] = None) -> Path:
    """
    Create a standardized log file path.

    Args:
        base_dir: Base directory for logs
        app_name: Name of the application
        timestamp: Optional timestamp string

    Returns:
        Path to the log file
    """
    if timestamp is None:
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    log_dir = base_dir / "logs"
    log_file = log_dir / f"{app_name}_{timestamp}.log"
    return log_file


class PerformanceLogger:
    """
    Logger for performance monitoring and profiling.

    This class provides utilities for logging performance metrics
    and timing information throughout the processing pipeline.
    """

    def __init__(self, logger_name: str = 'performance'):
        """
        Initialize the performance logger.

        Args:
            logger_name: Name for the performance logger
        """
        self.logger = get_logger(logger_name)

    def log_operation_start(self, operation: str, **context):
        """Log the start of an operation."""
        context_str = ' '.join(f'{k}={v}' for k, v in context.items())
        self.logger.info(f"START {operation} {context_str}")

    def log_operation_end(self, operation: str, duration: float, **context):
        """Log the end of an operation with duration."""
        context_str = ' '.join(f'{k}={v}' for k, v in context.items())
        self.logger.info(f"END {operation} duration={duration:.3f}s {context_str}")

    def log_memory_usage(self, operation: str, memory_mb: float, **context):
        """Log memory usage for an operation."""
        context_str = ' '.join(f'{k}={v}' for k, v in context.items())
        self.logger.info(f"MEMORY {operation} usage={memory_mb:.1f}MB {context_str}")

    def log_performance_metric(self, metric_name: str, value: Union[int, float], unit: str = '', **context):
        """Log a performance metric."""
        context_str = ' '.join(f'{k}={v}' for k, v in context.items())
        unit_str = f' {unit}' if unit else ''
        self.logger.info(f"METRIC {metric_name} value={value}{unit_str} {context_str}")
