#!/usr/bin/env python3
"""
Pre-commit hook for BugSpot analysis.
Runs before each commit to catch potential issues early.
"""

import sys
import subprocess
from pathlib import Path

def run_bugspot_analysis():
    """Run BugSpot analysis on staged files."""
    try:
        # Get staged Python files
        result = subprocess.run(
            ['git', 'diff', '--cached', '--name-only', '--diff-filter=ACM'],
            capture_output=True, text=True, check=True
        )
        
        staged_files = result.stdout.strip().split('\n')
        python_files = [f for f in staged_files if f.endswith('.py') and f]
        
        if not python_files:
            print("No Python files staged for commit. Skipping BugSpot analysis.")
            return True
        
        print(f"Running BugSpot analysis on {len(python_files)} staged Python files...")
        
        # Run BugSpot analysis
        bugspot_script = Path(__file__).parent / "bugspot_analyzer.py"
        result = subprocess.run(
            [sys.executable, str(bugspot_script), '.'],
            capture_output=True, text=True, check=False
        )
        
        if result.returncode != 0:
            print("❌ BugSpot analysis found issues:")
            print(result.stdout)
            print("\nPlease fix the issues above before committing.")
            print("\nTo run BugSpot analysis manually:")
            print(f"  python {bugspot_script} .")
            return False
        
        print("✅ BugSpot analysis passed!")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"Error running BugSpot analysis: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error: {e}")
        return False

def main():
    """Main function for pre-commit hook."""
    print("Running BugSpot pre-commit analysis...")
    
    success = run_bugspot_analysis()
    
    if not success:
        sys.exit(1)
    
    print("Pre-commit BugSpot analysis completed successfully.")

if __name__ == "__main__":
    main() 