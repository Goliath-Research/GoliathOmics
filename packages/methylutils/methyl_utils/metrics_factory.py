"""
Metrics factory for methylation analysis.

This module implements the factory pattern for creating and managing
statistical distance metrics. It provides a clean interface for
computing various distance measures between Beta distributions.
"""

import numpy as np
from typing import Dict, Any, Callable, List, Optional
try:
    from .gpu_detection import is_gpu_available
    from .metrics_core import (
        compute_jeffreys_divergence,
        compute_kl_divergence,
        compute_bhattacharyya_distance,
        compute_hellinger_distance,
        compute_wasserstein_distance,
        compute_jensen_shannon_distance,
        compute_weighted_jensen_shannon_distance
    )
    from .metric_validations import (
        validate_beta_parameters,
        validate_array_shapes
    )
except ImportError:
    # Fallback for direct imports
    from gpu_detection import is_gpu_available
    from metrics_core import (
        compute_jeffreys_divergence,
        compute_kl_divergence,
        compute_bhattacharyya_distance,
        compute_hellinger_distance,
        compute_wasserstein_distance,
        compute_jensen_shannon_distance,
        compute_weighted_jensen_shannon_distance
    )
    from metric_validations import (
        validate_beta_parameters,
        validate_array_shapes
    )


class MetricFactory:
    """
    Factory class for creating and managing statistical distance metrics.

    This class implements the factory pattern to provide a unified interface
    for computing various statistical distances between Beta distributions.
    It handles metric registration, validation, and computation dispatch.
    """

    def __init__(self):
        """Initialize the metric factory with available metrics."""
        self._metrics: Dict[str, Dict[str, Any]] = {}
        self._register_default_metrics()

    def _register_default_metrics(self):
        """Register all default metrics with their configurations."""
        # Standard metrics: all use (a1, b1, a2, b2, use_gpu)
        self._register_metric(
            name="jeffreys",
            function=compute_jeffreys_divergence,
            params=["a1", "b1", "a2", "b2", "use_gpu"],
            defaults={"use_gpu": True},
            description="Jeffreys divergence (symmetric KL divergence)"
        )

        self._register_metric(
            name="kl",
            function=compute_kl_divergence,
            params=["a1", "b1", "a2", "b2", "use_gpu"],
            defaults={"use_gpu": True},
            description="Kullback-Leibler divergence"
        )

        self._register_metric(
            name="bhattacharyya",
            function=compute_bhattacharyya_distance,
            params=["a1", "b1", "a2", "b2", "use_gpu"],
            defaults={"use_gpu": True},
            description="Bhattacharyya distance"
        )

        self._register_metric(
            name="hellinger",
            function=compute_hellinger_distance,
            params=["a1", "b1", "a2", "b2", "use_gpu"],
            defaults={"use_gpu": True},
            description="Hellinger distance"
        )

        self._register_metric(
            name="wasserstein",
            function=compute_wasserstein_distance,
            params=["a1", "b1", "a2", "b2", "use_gpu"],
            defaults={"use_gpu": True},
            description="Wasserstein distance (approximate)"
        )

        self._register_metric(
            name="jensen_shannon",
            function=compute_jensen_shannon_distance,
            params=["a1", "b1", "a2", "b2", "use_gpu"],
            defaults={"use_gpu": True},
            description="Jensen-Shannon distance"
        )

        self._register_metric(
            name="weighted_jensen_shannon",
            function=compute_weighted_jensen_shannon_distance,
            params=["a1", "b1", "a2", "b2", "use_gpu"],
            defaults={"use_gpu": True},
            description="Weighted Jensen-Shannon distance"
        )

    def _register_metric(self, name: str, function: Callable, params: List[str],
                        defaults: Dict[str, Any], description: str = ""):
        """
        Register a new metric with the factory.

        Args:
            name: Unique name for the metric
            function: The metric computation function
            params: List of parameter names in order
            defaults: Default values for parameters
            description: Human-readable description
        """
        self._metrics[name] = {
            "function": function,
            "params": params,
            "defaults": defaults,
            "description": description
        }

    def list_available_metrics(self) -> List[str]:
        """
        Get list of all available metric names.

        Returns:
            List of metric names
        """
        return list(self._metrics.keys())

    def get_metric_info(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a specific metric.

        Args:
            name: Name of the metric

        Returns:
            Dictionary with metric information or None if not found
        """
        return self._metrics.get(name)

    def compute_distance(self, metric_name: str, a1: np.ndarray, b1: np.ndarray,
                        a2: np.ndarray, b2: np.ndarray, **kwargs) -> np.ndarray:
        """
        Compute the specified distance metric between two Beta distributions.

        Args:
            metric_name: Name of the distance metric to use
            a1, b1: Parameters of the first Beta distribution
            a2, b2: Parameters of the second Beta distribution
            **kwargs: Additional keyword arguments (e.g., use_gpu, weights)

        Returns:
            Distance values as numpy array

        Raises:
            ValueError: If metric is unknown or parameters are invalid
        """
        # Check if metric exists
        if metric_name not in self._metrics:
            available = self.list_available_metrics()
            raise ValueError(f"Unknown distance metric: {metric_name}. Available metrics: {available}")

        # Get metric configuration
        config = self._metrics[metric_name]

        # Prepare parameter dictionary
        param_dict = {
            "a1": a1,
            "b1": b1,
            "a2": a2,
            "b2": b2
        }

        # Add defaults and override with provided values
        param_dict.update(config["defaults"])
        param_dict.update(kwargs)

        # Validate parameters
        self._validate_parameters(param_dict)

        # Extract parameters in the correct order
        call_params = [param_dict[param_name] for param_name in config["params"]]

        # Call the metric function
        return config["function"](*call_params)

    def _validate_parameters(self, param_dict: Dict[str, Any]):
        """
        Validate input parameters for metric computation.

        Args:
            param_dict: Dictionary of parameter names and values

        Raises:
            ValueError: If parameters are invalid
        """
        # Extract array parameters for validation
        array_params = []
        array_names = []

        for name, value in param_dict.items():
            if isinstance(value, np.ndarray) and name != "use_gpu":
                array_params.append(value)
                array_names.append(name)

        # Validate array shapes
        if array_params:
            validate_array_shapes(array_params, array_names)

            # Validate Beta parameters (skip if arrays are empty)
            if array_params[0].size > 0:
                # Group parameters by distribution
                for i in range(0, len(array_names), 2):
                    if i + 1 < len(array_names):
                        a_param = array_names[i]
                        b_param = array_names[i + 1]
                        validate_beta_parameters(
                            param_dict[a_param],
                            param_dict[b_param],
                            a_param,
                            b_param
                        )

    def create_metric_computer(self, metric_name: str, **defaults) -> Callable:
        """
        Create a metric computer function with pre-configured defaults.

        Args:
            metric_name: Name of the metric to create a computer for
            **defaults: Default parameter values

        Returns:
            Function that computes the metric with the given defaults

        Example:
            jsd_computer = factory.create_metric_computer("jensen_shannon", use_gpu=False)
            result = jsd_computer(a1, b1, a2, b2)
        """
        def metric_computer(a1: np.ndarray, b1: np.ndarray,
                           a2: np.ndarray, b2: np.ndarray, **kwargs) -> np.ndarray:
            # Merge defaults with provided kwargs
            call_kwargs = defaults.copy()
            call_kwargs.update(kwargs)
            return self.compute_distance(metric_name, a1, b1, a2, b2, **call_kwargs)

        return metric_computer


# Global factory instance
_metric_factory = None

def get_metric_factory() -> MetricFactory:
    """
    Get the global metric factory instance (singleton pattern).

    Returns:
        The global MetricFactory instance
    """
    global _metric_factory
    if _metric_factory is None:
        _metric_factory = MetricFactory()
    return _metric_factory


def auto_compute_distance(a1: np.ndarray, b1: np.ndarray, a2: np.ndarray,
                         b2: np.ndarray, metric: str = "jensen_shannon",
                         **kwargs) -> np.ndarray:
    """
    Compute distance using the best available backend (convenience function).

    This function automatically detects GPU availability and uses the
    global metric factory to compute the requested distance metric.

    Args:
        a1, b1: Parameters of the first Beta distribution
        a2, b2: Parameters of the second Beta distribution
        metric: Type of distance to compute
        **kwargs: Additional keyword arguments passed to the metric function

    Returns:
        Distance values as numpy array

    Raises:
        ValueError: If metric is unknown or parameters are invalid
    """
    factory = get_metric_factory()

    # Set default GPU usage based on availability
    if "use_gpu" not in kwargs:
        kwargs["use_gpu"] = is_gpu_available()

    return factory.compute_distance(metric, a1, b1, a2, b2, **kwargs)


__all__ = [
    "MetricFactory",
    "get_metric_factory",
    "auto_compute_distance"
]
