"""
MethylValidation: Monte Carlo validation runner for MethylPipeline.

Runs repeated stratified train/validation splits, executes Centroid → Detector
→ Classifier → Validator per iteration, and aggregates MethylValidator metrics
into empirical distributions.
"""

__version__ = "0.1.0"
