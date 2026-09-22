#!/usr/bin/env python3
"""
Create MethylDetector models for all chromosomes.

This script takes a single config.json file and generates configs for all chromosomes,
processing each chromosome with all contexts (CG, CHG, CHH) in a single run.
Each chromosome gets its own log file (output-{chrom}.log).

Usage:
    python create_all_models.py <config.json>
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional

try:
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn, TaskID
    from rich.console import Console
    from rich.table import Table
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    print("Warning: rich library not available. Falling back to simple progress display.")


def get_all_chromosomes(chromosomes: Optional[List[str]] = None) -> List[str]:
    """Generate list of chromosomes to process."""
    if chromosomes is None:
        chromosomes = [str(i) for i in range(1, 23)] + ['X', 'Y']  # 1-22, X, Y
    return chromosomes


def modify_config_for_chromosome(config: Dict[str, Any], chromosome: str, all_contexts: List[str] = None) -> Dict[str, Any]:
    """Modify the config to use the specified chromosome with all contexts."""
    if all_contexts is None:
        all_contexts = ['CG', 'CHG', 'CHH']
    
    # Create a deep copy of the config
    new_config = config.copy()
    
    # Handle different config formats
    if 'chromosome' in new_config and 'contexts' in new_config:
        # Format 1: Uses chromosome and contexts fields
        new_config['chromosome'] = chromosome
        new_config['contexts'] = all_contexts  # All contexts in one run
    elif 'centroid1_dir' in new_config and 'centroid2_dir' in new_config:
        # Format 2: Uses centroid directories (already supports multi-context)
        new_config['chromosome'] = chromosome
        if 'contexts' not in new_config:
            new_config['contexts'] = all_contexts
    elif 'centroid1_path' in new_config and 'centroid2_path' in new_config:
        # Format 3: Old format with specific paths - convert to directory format if possible
        # This is a fallback, ideally configs should use centroid1_dir/centroid2_dir
        old_path1 = Path(new_config['centroid1_path'])
        old_path2 = Path(new_config['centroid2_path'])
        # Extract base directory
        new_config['centroid1_dir'] = str(old_path1.parent)
        new_config['centroid2_dir'] = str(old_path2.parent)
        new_config['chromosome'] = chromosome
        new_config['contexts'] = all_contexts
        # Remove old path fields
        new_config.pop('centroid1_path', None)
        new_config.pop('centroid2_path', None)
    
    return new_config


def execute_modeler_script_with_config(config_dict: Dict[str, Any], modeler_script_path: str, log_file: Optional[Path] = None, suppress_output: bool = True) -> subprocess.CompletedProcess:
    """Execute the modeler script with a config dictionary."""
    import tempfile
    import uuid
    import time
    
    # Create temporary config file in current directory (accessible to Docker container)
    temp_filename = f"temp_config_{uuid.uuid4().hex[:8]}.json"
    temp_config_path = os.path.join(os.getcwd(), temp_filename)
    
    try:
        # Write config to temporary file
        with open(temp_config_path, 'w') as f:
            json.dump(config_dict, f, indent=2)
        
        # Verify file was created
        if not os.path.exists(temp_config_path):
            raise RuntimeError(f"Failed to create temporary config file: {temp_config_path}")
        
        # Small delay to ensure file is written
        time.sleep(0.1)
        
        # Build command with log file if specified
        cmd = [modeler_script_path, temp_filename]
        if log_file is not None:
            cmd.extend(['--log-file', str(log_file)])
        
        # Suppress stdout/stderr to avoid progress bar conflicts with Rich
        # All output should go to log_file anyway, but we still capture stderr for errors
        if suppress_output:
            # Set environment variables to disable Rich output from modeler script
            env = os.environ.copy()
            env['TERM'] = 'dumb'  # Tell programs this isn't a smart terminal
            env['NO_COLOR'] = '1'  # Disable color output
            env['FORCE_COLOR'] = '0'  # Disable forced color
            
            # Capture stderr for error messages, but suppress stdout
            # This prevents Rich progress bars from the modeler script from interfering
            result = subprocess.run(
                cmd,
                text=True,
                stdout=subprocess.DEVNULL,  # Always suppress stdout to avoid Rich conflicts
                stderr=subprocess.PIPE,     # Capture stderr for error reporting
                env=env                     # Use modified environment
            )
        else:
            result = subprocess.run(cmd, capture_output=True, text=True)
        return result
    finally:
        # Clean up temporary file after execution completes
        try:
            if os.path.exists(temp_config_path):
                os.unlink(temp_config_path)
        except OSError:
            pass  # File might already be deleted


def main():
    parser = argparse.ArgumentParser(
        description="Create MethylDetector models for all chromosomes (processing all contexts per chromosome)"
    )
    parser.add_argument(
        "config_file",
        help="Path to the original config.json file"
    )
    parser.add_argument(
        "--modeler-script",
        default="./modeler",
        help="Path to the modeler script (default: ./modeler)"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory to save log files (default: same directory as input config)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate configs but don't execute modeler script"
    )
    parser.add_argument(
        "--chromosomes",
        nargs="+",
        help="Specific chromosomes to process (e.g., --chromosomes 1 2 3 X). Default: all chromosomes 1-22, X, Y"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )
    
    args = parser.parse_args()
    
    # Validate input config file
    if not os.path.exists(args.config_file):
        print(f"Error: Config file '{args.config_file}' not found.", file=sys.stderr)
        sys.exit(1)
    
    # Validate modeler script
    if not args.dry_run and not os.path.exists(args.modeler_script):
        print(f"Error: Modeler script '{args.modeler_script}' not found.", file=sys.stderr)
        sys.exit(1)
    
    # Load the original config
    try:
        with open(args.config_file, 'r') as f:
            original_config = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in config file: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: Failed to read config file: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Determine output directory for log files - use the output_dir from config
    # This ensures log files are written to the same directory as the results
    if args.output_dir:
        log_output_dir = Path(args.output_dir)
    elif 'output_dir' in original_config and original_config['output_dir']:
        log_output_dir = Path(original_config['output_dir'])
    else:
        # Fallback to config file directory if no output_dir in config
        log_output_dir = Path(args.config_file).parent
    
    log_output_dir.mkdir(parents=True, exist_ok=True)
    
    # Determine chromosomes to process
    if args.chromosomes:
        chromosomes = args.chromosomes
    else:
        chromosomes = None  # Will use default (1-22, X, Y)
    
    # Get list of chromosomes to process
    chromosome_list = get_all_chromosomes(chromosomes)
    
    # Process all chromosomes including the one in the config
    # (The config is used as a template, and each chromosome gets processed)
    print(f"Processing {len(chromosome_list)} chromosomes (all contexts per chromosome)")
    if args.dry_run:
        print("Dry run mode: will generate configs but not execute")
    
    # Generate configs for each chromosome
    chromosome_configs = []
    for chrom in chromosome_list:
        modified_config = modify_config_for_chromosome(original_config, chrom)
        # Write log file to the same output_dir as the results
        log_file = log_output_dir / f"output-{chrom}.log"
        chromosome_configs.append((chrom, modified_config, log_file))
    
    if args.dry_run:
        print(f"\nGenerated {len(chromosome_configs)} chromosome configs:")
        for chrom, config, log_file in chromosome_configs:
            print(f"  Chromosome {chrom}: contexts={config.get('contexts', ['CG', 'CHG', 'CHH'])}, log={log_file}")
        return
    
    # Execute md scripts sequentially (one at a time) to avoid progress bar conflicts
    if RICH_AVAILABLE:
        import sys
        console = Console(file=sys.stderr, force_terminal=True, legacy_windows=False)
        
        # Create a single progress bar that tracks overall progress
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total})"),
            TimeElapsedColumn(),
            console=console,
            refresh_per_second=4,
            redirect_stdout=False,
            redirect_stderr=False
        ) as progress:
            
            # Create single task for overall progress
            total_chromosomes = len(chromosome_configs)
            overall_task = progress.add_task(
                f"[cyan]Processing chromosomes...[/cyan]",
                total=total_chromosomes
            )
            
            # Process each chromosome sequentially (one at a time)
            results = {}
            for i, (chrom, config, log_file) in enumerate(chromosome_configs, 1):
                # Update description to show current chromosome
                progress.update(
                    overall_task,
                    description=f"[yellow]Processing chromosome {chrom} ({i}/{total_chromosomes})...[/yellow]"
                )
                
                # Execute modeler script (suppress its output to avoid conflicts)
                result = execute_modeler_script_with_config(config, args.modeler_script, log_file, suppress_output=True)
                
                # Update progress based on result with immediate visual feedback
                if result.returncode == 0:
                    results[chrom] = {'success': True, 'error': None}
                    # Show green success immediately
                    progress.update(
                        overall_task,
                        description=f"[green]✓ Chromosome {chrom} completed ({i}/{total_chromosomes})[/green]",
                        advance=1
                    )
                else:
                    error_msg = result.stderr.strip() if result.stderr else f"Exit code: {result.returncode}"
                    results[chrom] = {'success': False, 'error': error_msg}
                    # Show red failure immediately
                    progress.update(
                        overall_task,
                        description=f"[red]✗ Chromosome {chrom} failed ({i}/{total_chromosomes})[/red]",
                        advance=1
                    )
                    console.print(f"[red]✗ Chromosome {chrom} failed:[/red] {error_msg}")
            
            # Final update
            success_count = sum(1 for r in results.values() if r['success'])
            progress.update(
                overall_task,
                description=f"[green]Completed: {success_count}/{total_chromosomes} successful[/green]"
            )
        
        # Print summary table
        console.print("\n" + "="*60)
        console.print("[bold]Summary[/bold]")
        console.print("="*60)
        
        table = Table(show_header=True, header_style="bold")
        table.add_column("Chromosome", style="cyan")
        table.add_column("Status", justify="center")
        table.add_column("Log File")
        
        success_count = 0
        for chrom, config, log_file in chromosome_configs:
            result = results[chrom]
            if result['success']:
                status = "[green]✓ Success[/green]"
                success_count += 1
            else:
                status = "[red]✗ Failed[/red]"
            table.add_row(chrom, status, str(log_file))
        
        console.print(table)
        console.print(f"\n[bold]Completed: {success_count}/{len(chromosome_configs)} successful[/bold]")
        
    else:
        # Fallback to simple progress display
        print(f"\nExecuting modeler scripts for {len(chromosome_configs)} chromosomes...")
        success_count = 0
        
        for i, (chrom, config, log_file) in enumerate(chromosome_configs, 1):
            print(f"[{i:2d}/{len(chromosome_configs)}] Processing chromosome {chrom}...")
            print(f"  Log file: {log_file}")
            
            result = execute_modeler_script_with_config(config, args.modeler_script, log_file)
            
            if result.returncode == 0:
                print(f"  ✅ Chromosome {chrom} completed successfully")
                success_count += 1
            else:
                print(f"  ❌ Chromosome {chrom} failed with return code {result.returncode}")
                if result.stderr:
                    print(f"  Error: {result.stderr.strip()}")
        
        print(f"\n{'='*60}")
        print(f"Summary: {success_count}/{len(chromosome_configs)} chromosomes completed successfully")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
