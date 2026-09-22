"""Re-export sample prep lifecycle helpers from ``ops`` (Admin CLI import path)."""

from ops.sample_lifecycle import start_sample_prep  # noqa: F401

__all__ = ["start_sample_prep"]
