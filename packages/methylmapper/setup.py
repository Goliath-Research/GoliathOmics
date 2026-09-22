#!/usr/bin/env python3
"""
Setup script for MethylMapper
"""

from setuptools import setup, find_packages
from pathlib import Path

# Read the README file
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text() if readme_file.exists() else ""

setup(
    name="methyl_mapper",
    version="0.1.0",
    author="MethylPipeline Team",
    description="Project-aware DMP-to-gene mapping with bedtools as the primary workflow",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(),
    python_requires=">=3.10,<3.15",
    install_requires=[
        "sqlalchemy>=1.4.0",
        "pandas>=1.3.0",
        "numpy>=1.20.0",
        "pydantic>=2.0.0",
        "pyodbc>=4.0.0",  # For Azure SQL ODBC connection
        "requests>=2.25.0",  # For API calls (Grok, DisGeNET)
        "cryptography>=3.4.0",  # For encrypted credential storage
    ],
    entry_points={
        "console_scripts": [
            "methyl-mapper=methyl_mapper.cli:main",
            "methyl_mapper=methyl_mapper.cli:main",
            "methyl_mapper_bedtools=methyl_mapper.cli:main_bedtools",
            "methyl_mapper_credentials=methyl_mapper.cli:main_credentials",
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

