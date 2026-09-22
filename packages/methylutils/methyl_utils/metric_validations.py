"""
Validation utilities for methylation analysis metrics.

This module provides validation functions for metric computations,
ensuring data integrity and providing meaningful error messages.
"""

import numpy as np
from typing import List


def validate_beta_parameters(a: np.ndarray, b: np.ndarray, param_name_a: str = "a", param_name_b: str = "b") -> None:
    """
    Validate Beta distribution parameters.

    Args:
        a: Alpha parameters array
        b: Beta parameters array
        param_name_a: Name of alpha parameter for error messages
        param_name_b: Name of beta parameter for error messages

    Raises:
        ValueError: If parameters have invalid shapes or values
    """
    if a.shape != b.shape:
        raise ValueError(f"Parameter shapes don't match: {param_name_a} {a.shape} vs {param_name_b} {b.shape}")

    if a.size == 0:
        raise ValueError("Input arrays cannot be empty")

    if np.any(a <= 0):
        raise ValueError(f"All values in parameter {param_name_a} must be positive")

    if np.any(b <= 0):
        raise ValueError(f"All values in parameter {param_name_b} must be positive")


def validate_array_shapes(arrays: List[np.ndarray], names: List[str]) -> None:
    """
    Validate that all arrays have the same shape.

    Args:
        arrays: List of arrays to validate
        names: List of parameter names for error messages

    Raises:
        ValueError: If arrays have different shapes
    """
    if not arrays:
        return

    reference_shape = arrays[0].shape
    reference_name = names[0] if names else "first array"

    for i, (arr, name) in enumerate(zip(arrays[1:], names[1:]), 1):
        if arr.shape != reference_shape:
            raise ValueError(f"Parameter {name} shape {arr.shape} does not match {reference_name} shape {reference_shape}")


def validate_methylation_data(m: np.ndarray, n: np.ndarray) -> None:
    """
    Validate methylation level and coverage data.

    Args:
        m: Methylation levels array (should be in [0, 1])
        n: Coverage values array (should be positive integers)

    Raises:
        ValueError: If data is invalid
    """
    if m.shape != n.shape:
        raise ValueError(f"Methylation array shape {m.shape} does not match coverage array shape {n.shape}")

    if m.size == 0:
        raise ValueError("Input arrays cannot be empty")

    if not np.issubdtype(m.dtype, np.floating) and not np.issubdtype(m.dtype, np.integer):
        raise ValueError("Methylation levels must be numeric")

    if not np.issubdtype(n.dtype, np.floating) and not np.issubdtype(n.dtype, np.integer):
        raise ValueError("Coverage values must be numeric")

    # Check for values outside valid ranges (with tolerance for floating point precision)
    # Use 1e-6 tolerance to handle typical floating point rounding errors from Beta calculations
    EPSILON = 1e-6
    if np.any((m < -EPSILON) | (m > 1.0 + EPSILON)):
        raise ValueError("Methylation levels must be in range [0, 1]")

    if np.any(n < 0):
        raise ValueError("Coverage values must be non-negative")


def validate_weights(weights: np.ndarray) -> None:
    """
    Validate weight array for weighted computations.

    Args:
        weights: Weight array that should sum to 1.0

    Raises:
        ValueError: If weights are invalid
    """
    if weights.size == 0:
        raise ValueError("Weights array cannot be empty")

    if weights.shape != (2,):
        raise ValueError(f"Weights must be a 2-element array, got shape {weights.shape}")

    if not np.isclose(weights.sum(), 1.0, atol=1e-6):
        raise ValueError(f"Weights must sum to 1.0, got sum {weights.sum()}")

    if np.any(weights < 0):
        raise ValueError("All weights must be non-negative")


def validate_sample_data(m: np.ndarray, n: np.ndarray, alpha_c: np.ndarray, beta_c: np.ndarray) -> None:
    """
    Validate sample data for centroid JSD computation.

    Args:
        m: Methylation levels for sample positions
        n: Coverages for sample positions
        alpha_c: Centroid alpha parameters
        beta_c: Centroid beta parameters

    Raises:
        ValueError: If data is invalid
    """
    # Validate methylation and coverage data
    validate_methylation_data(m, n)

    # Validate centroid parameters
    validate_beta_parameters(alpha_c, beta_c, "alpha_c", "beta_c")

    # Check compatibility
    if m.shape != alpha_c.shape:
        raise ValueError(f"Sample methylation shape {m.shape} does not match centroid alpha shape {alpha_c.shape}")


__all__ = [
    "validate_beta_parameters",
    "validate_array_shapes",
    "validate_methylation_data",
    "validate_weights",
    "validate_sample_data"
]
