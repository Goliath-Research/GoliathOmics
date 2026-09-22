"""Setup script for MethylCluster package."""

from setuptools import setup, find_packages
from pathlib import Path

# Read README for long description
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text() if readme_file.exists() else ""

setup(
    name="methyl_cluster",
    version="1.0.0",
    description="Project-aware methylation sample clustering for MethylPipeline",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="MethylPipeline Team",
    url="https://github.com/Goliath-Research/GoliathWorkflow",
    packages=find_packages(),
    python_requires=">=3.10,<3.15",
    install_requires=[
        "numpy>=1.21.0",
        "scipy>=1.7.0",
        "h5py>=3.7.0",
        "hdf5plugin>=0.2.0",
        "hdbscan>=0.8.33",
        "scikit-learn>=1.0.0",
        "plotly>=5.0.0",
        "matplotlib>=3.5.0",
        "pydantic>=2.0.0",
    ],
    entry_points={
        'console_scripts': [
            'methyl-cluster=methyl_cluster.cli:main',
            'methyl_cluster=methyl_cluster.cli:main',
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
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

