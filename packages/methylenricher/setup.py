#!/usr/bin/env python3
"""
Setup script for MethylEnricher
"""

from setuptools import setup, find_packages
from pathlib import Path

# Read the README file
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text() if readme_file.exists() else ""

setup(
    name="methyl_enricher",
    version="0.1.0",
    author="MethylDetector Team",
    description="Gene enrichment analysis tool for methylation DMPs",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(),
    python_requires=">=3.10,<3.15",
    install_requires=[
        "gseapy>=1.1.0",
        "pandas>=2.1.0,<3.0.0",
        "numpy>=2.0.0,<3.0.0",
        "networkx>=3.0",
        "python-louvain>=0.16",
    ],
    extras_require={
        "dash": [
            "dash>=2.17.0",
            "dash-cytoscape>=1.0.2",
        ],
    },
    entry_points={
        "console_scripts": [
            "methyl_enricher=methyl_enricher.cli:main",
            "methyl-enricher-network-discovery=methyl_enricher.network_discovery_cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering :: Bio-Informatics",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
    ],
)

