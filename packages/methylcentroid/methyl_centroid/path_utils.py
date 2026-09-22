"""
Path utilities for MethylPipeline projects.

This module provides utilities to locate resources across the MethylPipeline
workspace using the METHYLPIPELINE environment variable.
"""

import os
from pathlib import Path
from typing import Optional


def get_methylpipeline_root() -> Path:
    """
    Get the root directory of the MethylPipeline workspace.
    
    Returns:
        Path to METHYLPIPELINE root directory
        
    Raises:
        EnvironmentError: If METHYLPIPELINE environment variable is not set
    """
    methylpipeline = os.environ.get('METHYLPIPELINE')
    if not methylpipeline:
        raise EnvironmentError(
            "METHYLPIPELINE environment variable is not set.\n"
            "Please run: source setup_env.sh"
        )
    return Path(methylpipeline)


def get_project_path(project_name: str) -> Path:
    """
    Get the path to a specific project within MethylPipeline.
    
    Args:
        project_name: Name of the project (e.g., 'methylutils', 'methylcentroid')
        
    Returns:
        Path to the project directory
    """
    root = get_methylpipeline_root()
    return root / "packages" / project_name


def _resolve_module_dir(project_path: Path, project_name: str) -> Path:
    """
    Resolve the module directory inside a project.

    Tries both the project name and an underscore variant (e.g., methylcentroid -> methyl_centroid).
    """
    candidates = [
        project_path / project_name,
        project_path / project_name.replace("methyl", "methyl_"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return project_path / project_name


def get_config_path(project_name: str, config_name: str) -> Path:
    """
    Get the path to a configuration file.
    
    Args:
        project_name: Name of the project
        config_name: Name of the config file (e.g., 'pb-cancer_batch_config.json')
        
    Returns:
        Path to the configuration file
    """
    project_path = get_project_path(project_name)
    module_dir = _resolve_module_dir(project_path, project_name)
    candidates = [
        project_path / "configs",
        module_dir / "configs",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate / config_name
    return candidates[0] / config_name


def get_examples_path(project_name: str) -> Path:
    """
    Get the path to a project's examples directory.
    
    Args:
        project_name: Name of the project
        
    Returns:
        Path to the examples directory
    """
    project_path = get_project_path(project_name)
    module_dir = _resolve_module_dir(project_path, project_name)
    candidates = [
        project_path / "examples",
        module_dir / "examples",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def get_data_path(relative_path: Optional[str] = None) -> Path:
    """
    Get the path to data directory or a specific data file.
    
    Args:
        relative_path: Optional relative path within the data directory
        
    Returns:
        Path to data directory or specific data file
    """
    root = get_methylpipeline_root()
    data_path = root / "data"
    
    if relative_path:
        return data_path / relative_path
    return data_path


def ensure_methylpipeline_env() -> bool:
    """
    Check if METHYLPIPELINE environment variable is set.
    
    Returns:
        True if set, False otherwise
    """
    return 'METHYLPIPELINE' in os.environ


def print_methylpipeline_info():
    """Print information about the MethylPipeline environment."""
    try:
        root = get_methylpipeline_root()
        print("MethylPipeline Environment:")
        print(f"  Root: {root}")
        print(f"  Exists: {root.exists()}")
        
        projects = ["methylutils", "methylcentroid"]
        print("\nProjects:")
        for project in projects:
            project_path = get_project_path(project)
            print(f"  - {project}: {project_path} ({'✓' if project_path.exists() else '✗'})")
            
    except EnvironmentError as e:
        print(f"Error: {e}")
        print("\nTo set up the environment, run:")
        print("  source setup_env.sh")


if __name__ == '__main__':
    print_methylpipeline_info()

