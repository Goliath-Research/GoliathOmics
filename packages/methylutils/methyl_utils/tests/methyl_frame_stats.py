"""
Helper module for computing statistics and generating histograms for MethylFrame classes.

This module provides reusable functions for:
- Loading samples from CSV files or config.json
- Computing global statistics (averages, totals, histograms)
- Generating interactive Plotly HTML visualizations
"""

import sys
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
import json
import csv
import numpy as np
import pandas as pd

# Add parent directory to path to find methyl_utils package
_script_dir = Path(__file__).resolve().parent
_packages_dir = _script_dir.parent.parent.parent.parent / "packages" / "methylutils"
if _packages_dir.exists() and str(_packages_dir) not in sys.path:
    sys.path.insert(0, str(_packages_dir))

try:
    import plotly.graph_objects as go
    import plotly.express as px
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

from methyl_utils.core.methyl_frame import MethylSample, MethylCentroid


def load_samples_from_csv(csv_path: Path, input_dir: Path, 
                         chromosomes: Optional[List[str]] = None,
                         contexts: Optional[List[str]] = None) -> List[MethylSample]:
    """
    Load samples from a CSV file containing sample folder names.
    
    CSV file should have a single column (no header) with sample folder names.
    For each folder, loads all {chrom}-{context}.h5 files from the sample directory.
    
    Args:
        csv_path: Path to CSV file with sample folder names (single column, no header)
        input_dir: Base directory containing sample folders
        chromosomes: Optional list of chromosomes to load (e.g., ['1', '2', 'X'])
        contexts: Optional list of contexts to load (e.g., ['CG', 'CHG', 'CHH'])
        
    Returns:
        List of MethylSample objects (one per chromosome-context combination per sample)
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory not found: {input_dir}")
    
    # Read CSV file (single column, no header)
    sample_folders = []
    with open(csv_path, 'r') as f:
        reader = csv.reader(f)
        for row in reader:
            if row and row[0].strip():  # Skip empty rows
                sample_folders.append(row[0].strip())
    
    if not sample_folders:
        raise ValueError(f"No sample folders found in CSV file: {csv_path}")
    
    # Default chromosomes and contexts if not specified
    if chromosomes is None:
        chromosomes = [str(i) for i in range(1, 23)] + ['X', 'Y']
    if contexts is None:
        contexts = ['CG', 'CHG', 'CHH']
    
    samples = []
    
    for folder_name in sample_folders:
        sample_dir = input_dir / folder_name
        
        if not sample_dir.exists():
            print(f"Warning: Sample directory not found: {sample_dir}")
            continue
        
        # Find all chromosome-context files
        for chrom in chromosomes:
            for ctx in contexts:
                h5_file = sample_dir / f"{chrom}-{ctx}.h5"
                
                if h5_file.exists():
                    try:
                        sample = MethylSample.load_from_h5(h5_file)
                        # Add metadata to identify the sample
                        sample.metadata = sample.metadata or {}
                        sample.metadata['sample_folder'] = folder_name
                        sample.metadata['chromosome'] = chrom
                        sample.metadata['context'] = ctx
                        samples.append(sample)
                    except Exception as e:
                        print(f"Warning: Failed to load {h5_file}: {e}")
                        continue
    
    return samples


def load_samples_from_config(config_path: Path,
                             chromosomes: Optional[List[str]] = None,
                             contexts: Optional[List[str]] = None) -> List[MethylSample]:
    """
    Load samples from a config.json file.
    
    Config file should have a 'samples' field containing a list of paths.
    Each path can be either:
    - A directory containing {chrom}-{context}.h5 files
    - A direct path to an .h5 file
    
    Args:
        config_path: Path to config.json file
        chromosomes: Optional list of chromosomes to load
        contexts: Optional list of contexts to load
        
    Returns:
        List of MethylSample objects
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    with open(config_path, 'r') as f:
        config = json.load(f)
    
    sample_paths = config.get('samples', [])
    if not sample_paths:
        raise ValueError(f"No 'samples' field found in config file: {config_path}")
    
    # Default chromosomes and contexts if not specified
    if chromosomes is None:
        chromosomes = [str(i) for i in range(1, 23)] + ['X', 'Y']
    if contexts is None:
        contexts = ['CG', 'CHG', 'CHH']
    
    samples = []
    
    for sample_path_str in sample_paths:
        sample_path = Path(sample_path_str)
        
        if not sample_path.exists():
            print(f"Warning: Sample path not found: {sample_path}")
            continue
        
        # Check if it's a direct H5 file
        if sample_path.is_file() and sample_path.suffix.lower() == '.h5':
            try:
                sample = MethylSample.load_from_h5(sample_path)
                sample.metadata = sample.metadata or {}
                sample.metadata['sample_path'] = str(sample_path)
                samples.append(sample)
            except Exception as e:
                print(f"Warning: Failed to load {sample_path}: {e}")
                continue
        
        # Otherwise, treat as directory
        elif sample_path.is_dir():
            for chrom in chromosomes:
                for ctx in contexts:
                    h5_file = sample_path / f"{chrom}-{ctx}.h5"
                    
                    if h5_file.exists():
                        try:
                            sample = MethylSample.load_from_h5(h5_file)
                            sample.metadata = sample.metadata or {}
                            sample.metadata['sample_path'] = str(sample_path)
                            sample.metadata['chromosome'] = chrom
                            sample.metadata['context'] = ctx
                            samples.append(sample)
                        except Exception as e:
                            print(f"Warning: Failed to load {h5_file}: {e}")
                            continue
    
    return samples


def compute_sample_statistics(sample: Union[MethylSample, MethylCentroid]) -> Dict[str, Any]:
    """
    Compute global statistics for a methylation sample or centroid.
    
    Args:
        sample: MethylSample or MethylCentroid instance
        
    Returns:
        Dictionary containing computed statistics
    """
    # Convert to CPU if needed
    sample_cpu = sample.to_cpu() if hasattr(sample, 'to_cpu') else sample
    
    # Get arrays
    mC_vals = np.asarray(sample_cpu.mC.values) if hasattr(sample_cpu.mC, 'values') else np.asarray(sample_cpu.mC)
    uC_vals = np.asarray(sample_cpu.uC.values) if hasattr(sample_cpu.uC, 'values') else np.asarray(sample_cpu.uC)
    coverage_vals = mC_vals + uC_vals
    
    # Compute methylation levels (handle division by zero) and ensure [0, 1] range
    with np.errstate(divide='ignore', invalid='ignore'):
        methylation_levels = np.where(
            coverage_vals > 0, 
            mC_vals.astype(np.float64) / coverage_vals.astype(np.float64), 
            0.0
        )
        # Clip to [0, 1] range to handle any numerical errors
        methylation_levels = np.clip(methylation_levels, 0.0, 1.0)
    
    # Basic statistics
    stats = {
        'position_count': len(sample_cpu.pos),
        'avg_mC': float(np.mean(mC_vals)),
        'avg_uC': float(np.mean(uC_vals)),
        'avg_coverage': float(np.mean(coverage_vals)),
        'avg_methylation_level': float(np.mean(methylation_levels[methylation_levels > 0])) if np.any(methylation_levels > 0) else 0.0,
        'min_methylation_level': float(np.min(methylation_levels[methylation_levels > 0])) if np.any(methylation_levels > 0) else 0.0,
        'max_methylation_level': float(np.max(methylation_levels[methylation_levels > 0])) if np.any(methylation_levels > 0) else 0.0,
        'total_mC': int(np.sum(mC_vals)),
        'total_uC': int(np.sum(uC_vals)),
        'total_coverage': int(np.sum(coverage_vals)),
        'min_coverage': int(np.min(coverage_vals)) if len(coverage_vals) > 0 else 0,
        'max_coverage': int(np.max(coverage_vals)) if len(coverage_vals) > 0 else 0,
        'median_coverage': float(np.median(coverage_vals)) if len(coverage_vals) > 0 else 0.0,
    }
    
    # Add centroid-specific statistics (MethylCentroid has N, Sx, Sx2)
    if isinstance(sample_cpu, MethylCentroid):
        N_vals = np.asarray(sample_cpu.N.values) if hasattr(sample_cpu.N, 'values') else np.asarray(sample_cpu.N)
        stats['avg_N'] = float(np.mean(N_vals))
        stats['min_N'] = int(np.min(N_vals)) if len(N_vals) > 0 else 0
        stats['max_N'] = int(np.max(N_vals)) if len(N_vals) > 0 else 0
        stats['total_samples'] = int(np.max(N_vals)) if len(N_vals) > 0 else 0
        Sx_vals = np.asarray(sample_cpu.Sx.values) if hasattr(sample_cpu.Sx, 'values') else np.asarray(sample_cpu.Sx)
        Sx2_vals = np.asarray(sample_cpu.Sx2.values) if hasattr(sample_cpu.Sx2, 'values') else np.asarray(sample_cpu.Sx2)
        stats['avg_Sx'] = float(np.mean(Sx_vals))
        stats['avg_Sx2'] = float(np.mean(Sx2_vals))
    
    # Add sample type
    stats['sample_type'] = sample_cpu.sample_type
    
    # Add metadata if available
    if hasattr(sample_cpu, 'metadata') and sample_cpu.metadata:
        stats['sample_name'] = sample_cpu.metadata.get('sample_folder') or sample_cpu.metadata.get('sample_path', 'unknown')
        if 'chromosome' in sample_cpu.metadata:
            stats['chromosome'] = sample_cpu.metadata['chromosome']
        if 'context' in sample_cpu.metadata:
            stats['context'] = sample_cpu.metadata['context']
    
    return stats


def create_histogram_html(data: np.ndarray, title: str, xlabel: str, output_path: Path, 
                          bins: Optional[int] = None, normalize: bool = True) -> None:
    """
    Create an interactive histogram using Plotly and save as HTML.
    
    Args:
        data: Array of data values to histogram
        title: Title for the histogram
        xlabel: Label for x-axis
        output_path: Path to save HTML file
        bins: Number of bins (auto if None)
        normalize: If True, normalize histogram to probability density (area = 1) for comparability
    """
    if not HAS_PLOTLY:
        raise ImportError("plotly is required for histogram generation. Install with: pip install plotly")
    
    # Filter out invalid values
    valid_data = data[np.isfinite(data)]
    
    if len(valid_data) == 0:
        print(f"Warning: No valid data for histogram {title}")
        return
    
    # Create histogram
    fig = go.Figure()
    
    # Use histnorm='probability density' for normalized histograms (area = 1)
    # This makes histograms comparable across different sample sizes
    histnorm = 'probability density' if normalize else None
    yaxis_title = 'Probability Density' if normalize else 'Frequency'
    
    fig.add_trace(go.Histogram(
        x=valid_data,
        nbinsx=bins,
        name=title,
        marker_color='steelblue',
        opacity=0.7,
        histnorm=histnorm
    ))
    
    fig.update_layout(
        title=title,
        xaxis_title=xlabel,
        yaxis_title=yaxis_title,
        template='plotly_white',
        hovermode='x unified'
    )
    
    # Save to HTML
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(output_path))
    print(f"Saved histogram: {output_path}")


def generate_all_histograms(sample: Union[MethylSample, MethylCentroid], 
                           output_dir: Path, sample_name: str) -> Dict[str, Path]:
    """
    Generate all histograms for a sample (mC, uC, coverage, methylation level).
    
    Args:
        sample: MethylSample or MethylCentroid instance
        output_dir: Directory to save histogram HTML files
        sample_name: Name identifier for the sample (used in filenames)
        
    Returns:
        Dictionary mapping metric name to output file path
    """
    if not HAS_PLOTLY:
        raise ImportError("plotly is required for histogram generation. Install with: pip install plotly")
    
    # Convert to CPU if needed
    sample_cpu = sample.to_cpu() if hasattr(sample, 'to_cpu') else sample
    
    # Get arrays
    mC_vals = np.asarray(sample_cpu.mC.values) if hasattr(sample_cpu.mC, 'values') else np.asarray(sample_cpu.mC)
    uC_vals = np.asarray(sample_cpu.uC.values) if hasattr(sample_cpu.uC, 'values') else np.asarray(sample_cpu.uC)
    coverage_vals = mC_vals + uC_vals
    
    # Compute methylation levels and ensure they're in [0, 1] range
    with np.errstate(divide='ignore', invalid='ignore'):
        methylation_levels = np.where(
            coverage_vals > 0, 
            mC_vals.astype(np.float64) / coverage_vals.astype(np.float64), 
            0.0
        )
        # Clip to [0, 1] range to handle any numerical errors
        methylation_levels = np.clip(methylation_levels, 0.0, 1.0)
        # Filter out zeros for histogram (optional - you may want to keep them)
        # methylation_levels = methylation_levels[methylation_levels > 0]
    
    output_paths = {}
    
    # Generate histograms
    histograms = [
        (mC_vals, 'mC', 'Methylated Count (mC)', 'mC_histogram'),
        (uC_vals, 'uC', 'Unmethylated Count (uC)', 'uC_histogram'),
        (coverage_vals, 'coverage', 'Coverage (mC + uC)', 'coverage_histogram'),
        (methylation_levels, 'methylation_level', 'Naive Methylation Level (mC / Coverage)', 'methylation_level_histogram'),
    ]
    
    for data, metric, xlabel, filename_base in histograms:
        output_path = output_dir / f"{sample_name}_{filename_base}.html"
        # Normalize all histograms for comparability
        create_histogram_html(data, f"{sample_name} - {xlabel}", xlabel, output_path, normalize=True)
        output_paths[metric] = output_path
    
    return output_paths


def create_mock_sample(chrom: str, context: str, n_positions: int = 1000, 
                      seed: Optional[int] = None) -> MethylSample:
    """
    Create a mock MethylSample for testing.
    
    Args:
        chrom: Chromosome identifier
        context: Context identifier (CG, CHG, CHH)
        n_positions: Number of genomic positions
        seed: Random seed for reproducibility
        
    Returns:
        MethylSample instance with realistic mock data
    """
    if seed is not None:
        np.random.seed(seed)
    
    # Generate random positions
    pos = np.sort(np.random.randint(1, 100_000_000, n_positions, dtype=np.uint32))
    pos = np.unique(pos)  # Ensure unique
    n_pos = len(pos)
    
    # Generate realistic coverage (skewed distribution)
    coverage = np.random.lognormal(mean=2.0, sigma=1.5, size=n_pos).astype(np.uint32)
    coverage = np.clip(coverage, 1, 1000)  # Cap at reasonable values
    
    # Generate realistic methylation levels (beta distribution)
    methylation_levels = np.random.beta(2, 2, n_pos)
    
    # Compute mC and uC
    mC = (coverage.astype(np.float64) * methylation_levels).astype(np.uint32)
    uC = coverage - mC
    
    # Generate tnc codes (simplified - just use context codes)
    from methyl_utils import CONTEXT_CG, CONTEXT_CHG, CONTEXT_CHH, CONTEXT_SHIFT
    context_codes = {
        'CG': CONTEXT_CG << CONTEXT_SHIFT,    # 0 << 5 = 0
        'CHG': CONTEXT_CHG << CONTEXT_SHIFT,  # 1 << 5 = 32
        'CHH': CONTEXT_CHH << CONTEXT_SHIFT   # 2 << 5 = 64
    }
    base_tnc = context_codes.get(context, 0)
    tnc = np.full(n_pos, base_tnc, dtype=np.uint8)
    
    # Create sample
    sample = MethylSample.from_sample_data(pos, mC, uC, tnc)
    sample.metadata = {
        'chromosome': chrom,
        'context': context,
        'sample_type': 'mock'
    }
    
    return sample

