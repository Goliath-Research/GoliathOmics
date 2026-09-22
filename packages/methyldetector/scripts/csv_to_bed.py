#!/usr/bin/env python3
"""
Convert MethylDetector CSV output to BED format for gene annotation.

Usage:
    python csv_to_bed.py <input_csv> [output_bed]

Example:
    python csv_to_bed.py dmps-1-1-biological.csv stage1.bed
    python csv_to_bed.py dmps-1-2-binary-search.csv > stage2.bed

BED Format (6-column):
    chr  start  end  name  score  strand
"""

import sys
import pandas as pd
from pathlib import Path


def csv_to_bed(csv_path: Path, output_path: Path = None, include_context: bool = True):
    """
    Convert MethylDetector CSV to BED format.
    
    Args:
        csv_path: Path to input CSV file
        output_path: Path to output BED file (None = stdout)
        include_context: Include context in the name field
    """
    # Read CSV
    df = pd.read_csv(csv_path)
    
    # Required columns
    if 'chromosome' not in df.columns or 'position' not in df.columns:
        print("ERROR: CSV must have 'chromosome' and 'position' columns", file=sys.stderr)
        sys.exit(1)
    
    # Prepare BED data
    bed_data = []
    
    for _, row in df.iterrows():
        # BED uses 0-based coordinates, position is typically 1-based
        start = row['position'] - 1
        end = row['position']
        
        # Chromosome (add 'chr' prefix if not present)
        chrom = str(row['chromosome'])
        if not chrom.startswith('chr'):
            chrom = f"chr{chrom}"
        
        # Name field: position + context + direction
        name_parts = [f"pos{row['position']}"]
        
        if include_context and 'context' in df.columns:
            name_parts.append(row['context'])
        
        if 'delta_sign' in df.columns:
            direction = 'hyper' if row['delta_sign'] > 0 else 'hypo'
            name_parts.append(direction)
        elif 'delta_mean' in df.columns:
            direction = 'hyper' if row['delta_mean'] > 0 else 'hypo'
            name_parts.append(direction)
        
        name = '_'.join(name_parts)
        
        # Score: use effect_size if available, otherwise use absolute delta_mean
        if 'effect_size' in df.columns:
            score = int(row['effect_size'] * 1000)  # Scale to integer
        elif 'delta_mean' in df.columns:
            score = int(abs(row['delta_mean']) * 1000)
        else:
            score = 1000
        
        # Cap score at 1000 (BED spec)
        score = min(score, 1000)
        
        # Strand: use '+' for hyper, '-' for hypo (arbitrary but useful)
        if 'delta_sign' in df.columns:
            strand = '+' if row['delta_sign'] > 0 else '-'
        elif 'delta_mean' in df.columns:
            strand = '+' if row['delta_mean'] > 0 else '-'
        else:
            strand = '.'
        
        bed_data.append([chrom, start, end, name, score, strand])
    
    # Create DataFrame
    bed_df = pd.DataFrame(bed_data, columns=['chrom', 'start', 'end', 'name', 'score', 'strand'])
    
    # Sort by chromosome and position
    bed_df = bed_df.sort_values(['chrom', 'start'])
    
    # Write to file or stdout
    if output_path:
        bed_df.to_csv(output_path, sep='\t', index=False, header=False)
        print(f"✓ Converted {len(bed_df)} positions to BED format: {output_path}", file=sys.stderr)
    else:
        bed_df.to_csv(sys.stdout, sep='\t', index=False, header=False)
    
    return bed_df


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    csv_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    
    if not csv_path.exists():
        print(f"ERROR: Input file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)
    
    csv_to_bed(csv_path, output_path)


if __name__ == "__main__":
    main()

