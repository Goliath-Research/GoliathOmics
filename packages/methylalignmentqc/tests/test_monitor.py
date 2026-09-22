import sys
from pathlib import Path

# Add package to path
package_root = Path(__file__).parent.parent
sys.path.append(str(package_root))

from methyl_alignment_qc import run_with_logging

def main():
    
    print("Running monitor test...")
    # Run the simulation with a known total size (approx matching the simulator's output)
    # Simulator emits: 9906648, 168396395, 316975713
    # Let's say total is 320,000,000
    run_with_logging(
        [sys.executable, "tests/simulate_fq2bam.py"], 
        "test_sample_001",
        total_input_size=320000000
    )

if __name__ == "__main__":
    main()
