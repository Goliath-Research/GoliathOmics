#!/usr/bin/env python3
"""
Compare the three DMP selection stages and generate summary statistics.

Usage:
    python compare_dmp_stages.py <output_dir> <chromosome>

Example:
    python compare_dmp_stages.py /path/to/detection/output 1
"""

import sys
import pandas as pd
from pathlib import Path
import numpy as np


def load_stage_csv(output_dir: Path, chromosome: str, stage: int) -> pd.DataFrame:
    """Load a stage CSV file."""
    stage_names = {
        1: "1-biological",
        2: "2-binary-search", 
        3: "3-differential-evolution"
    }
    
    csv_path = output_dir / f"dmps-{chromosome}-{stage_names[stage]}.csv"
    if not csv_path.exists():
        return None
    
    return pd.read_csv(csv_path)


def compare_stages(output_dir: Path, chromosome: str):
    """Compare the three DMP selection stages."""
    
    print("="*80)
    print("DMP STAGE COMPARISON REPORT")
    print("="*80)
    print(f"\nOutput Directory: {output_dir}")
    print(f"Chromosome: {chromosome}\n")
    
    # Load all stages
    stage1 = load_stage_csv(output_dir, chromosome, 1)
    stage2 = load_stage_csv(output_dir, chromosome, 2)
    stage3 = load_stage_csv(output_dir, chromosome, 3)
    
    if stage1 is None:
        print("❌ ERROR: Stage 1 (biological DMPs) CSV not found!")
        return
    
    # Basic counts
    print("📊 DMP COUNTS")
    print("-" * 80)
    print(f"Stage 1 (Biological DMPs):           {len(stage1):>10,} DMPs")
    
    if stage2 is not None:
        print(f"Stage 2 (Binary Search):             {len(stage2):>10,} DMPs  "
              f"({len(stage2)/len(stage1)*100:>5.1f}% retention)")
    else:
        print(f"Stage 2 (Binary Search):             {'N/A':>10}  (not generated)")
    
    if stage3 is not None:
        print(f"Stage 3 (Differential Evolution):    {len(stage3):>10,} DMPs  "
              f"({len(stage3)/len(stage1)*100:>5.1f}% retention)")
    else:
        print(f"Stage 3 (Differential Evolution):    {'N/A':>10}  (not generated)")
    
    # Context breakdown
    print("\n📍 CONTEXT BREAKDOWN")
    print("-" * 80)
    
    for stage_name, df in [("Stage 1", stage1), ("Stage 2", stage2), ("Stage 3", stage3)]:
        if df is None:
            continue
        
        print(f"\n{stage_name}:")
        if 'context' in df.columns:
            context_counts = df['context'].value_counts().sort_index()
            for ctx, count in context_counts.items():
                pct = count / len(df) * 100
                print(f"  {ctx:>4}: {count:>8,} DMPs  ({pct:>5.1f}%)")
        else:
            print("  (no context column)")
    
    # Effect size statistics
    print("\n📈 EFFECT SIZE STATISTICS")
    print("-" * 80)
    
    for stage_name, df in [("Stage 1", stage1), ("Stage 2", stage2), ("Stage 3", stage3)]:
        if df is None or 'effect_size' not in df.columns:
            continue
        
        print(f"\n{stage_name}:")
        print(f"  Mean:   {df['effect_size'].mean():.4f}")
        print(f"  Median: {df['effect_size'].median():.4f}")
        print(f"  Min:    {df['effect_size'].min():.4f}")
        print(f"  Max:    {df['effect_size'].max():.4f}")
        print(f"  StdDev: {df['effect_size'].std():.4f}")
    
    # Delta mean statistics
    print("\n📊 DELTA MEAN STATISTICS")
    print("-" * 80)
    
    for stage_name, df in [("Stage 1", stage1), ("Stage 2", stage2), ("Stage 3", stage3)]:
        if df is None or 'delta_mean' not in df.columns:
            continue
        
        print(f"\n{stage_name}:")
        print(f"  Mean Abs:  {df['delta_mean'].abs().mean():.4f}")
        print(f"  Median Abs:{df['delta_mean'].abs().median():.4f}")
        print(f"  Min:       {df['delta_mean'].min():.4f}")
        print(f"  Max:       {df['delta_mean'].max():.4f}")
        
        # Directional breakdown
        if 'delta_sign' in df.columns or 'mean1' in df.columns:
            if 'delta_sign' in df.columns:
                hyper = (df['delta_sign'] > 0).sum()
                hypo = (df['delta_sign'] < 0).sum()
            else:
                hyper = (df['delta_mean'] > 0).sum()
                hypo = (df['delta_mean'] < 0).sum()
            
            print(f"  Hypermethylated:   {hyper:>8,} ({hyper/len(df)*100:>5.1f}%)")
            print(f"  Hypomethylated:    {hypo:>8,} ({hypo/len(df)*100:>5.1f}%)")
    
    # Position overlap analysis
    if stage2 is not None and stage3 is not None:
        print("\n🔄 POSITION OVERLAP ANALYSIS")
        print("-" * 80)
        
        # Get position sets
        pos1 = set(zip(stage1['position'], stage1['context']))
        pos2 = set(zip(stage2['position'], stage2['context']))
        pos3 = set(zip(stage3['position'], stage3['context']))
        
        # Stage 1 → Stage 2
        lost_1_to_2 = len(pos1 - pos2)
        retained_1_to_2 = len(pos1 & pos2)
        print(f"\nStage 1 → Stage 2:")
        print(f"  Retained: {retained_1_to_2:>10,} positions ({retained_1_to_2/len(pos1)*100:>5.1f}%)")
        print(f"  Lost:     {lost_1_to_2:>10,} positions ({lost_1_to_2/len(pos1)*100:>5.1f}%)")
        
        # Stage 2 → Stage 3
        lost_2_to_3 = len(pos2 - pos3)
        retained_2_to_3 = len(pos2 & pos3)
        gained_2_to_3 = len(pos3 - pos2)
        print(f"\nStage 2 → Stage 3:")
        print(f"  Retained: {retained_2_to_3:>10,} positions ({retained_2_to_3/len(pos2)*100:>5.1f}%)")
        print(f"  Lost:     {lost_2_to_3:>10,} positions ({lost_2_to_3/len(pos2)*100:>5.1f}%)")
        print(f"  Gained:   {gained_2_to_3:>10,} positions ({gained_2_to_3/len(pos2)*100:>5.1f}%)")
        
        # Stage 1 → Stage 3
        lost_1_to_3 = len(pos1 - pos3)
        retained_1_to_3 = len(pos1 & pos3)
        print(f"\nStage 1 → Stage 3 (Overall):")
        print(f"  Retained: {retained_1_to_3:>10,} positions ({retained_1_to_3/len(pos1)*100:>5.1f}%)")
        print(f"  Lost:     {lost_1_to_3:>10,} positions ({lost_1_to_3/len(pos1)*100:>5.1f}%)")
    
    # Export summary to CSV
    summary_path = output_dir / f"stage_comparison_summary-{chromosome}.csv"
    
    summary_data = []
    for stage_num, stage_name, df in [(1, "Biological", stage1), 
                                       (2, "Binary Search", stage2), 
                                       (3, "Differential Evolution", stage3)]:
        if df is None:
            continue
        
        row = {
            'stage': stage_num,
            'stage_name': stage_name,
            'n_dmps': len(df),
            'retention_pct': len(df) / len(stage1) * 100,
        }
        
        if 'effect_size' in df.columns:
            row['mean_effect_size'] = df['effect_size'].mean()
            row['median_effect_size'] = df['effect_size'].median()
        
        if 'delta_mean' in df.columns:
            row['mean_abs_delta_mean'] = df['delta_mean'].abs().mean()
            row['median_abs_delta_mean'] = df['delta_mean'].abs().median()
        
        if 'context' in df.columns:
            for ctx in ['CG', 'CHG', 'CHH']:
                count = (df['context'] == ctx).sum()
                row[f'n_dmps_{ctx}'] = count
        
        summary_data.append(row)
    
    summary_df = pd.DataFrame(summary_data)
    summary_df.to_csv(summary_path, index=False)
    
    print(f"\n💾 Summary exported to: {summary_path}")
    print("="*80)


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    
    output_dir = Path(sys.argv[1])
    chromosome = sys.argv[2]
    
    if not output_dir.exists():
        print(f"❌ ERROR: Output directory does not exist: {output_dir}")
        sys.exit(1)
    
    compare_stages(output_dir, chromosome)


if __name__ == "__main__":
    main()

