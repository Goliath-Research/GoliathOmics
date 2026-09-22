"""
MethylClassifier - Command Line Tool for Methylation-Based Sample Classification

A Bayesian classifier for methylation samples that can classify samples against
multiple centroids and handle various methylation data formats.

Author: MethylClassifier Development Team
Version: 0.1.0
"""

__version__ = "0.1.0"
__author__ = "MethylClassifier Development Team"

from .core.classifier import MethylClassifier
from .models.config import ClassifierConfig

__all__ = ['MethylClassifier', 'ClassifierConfig']
