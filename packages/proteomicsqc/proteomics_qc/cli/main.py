"""CLI: proteomics-qc SAMPLE_ID --sample-dir DIR."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from ..core.writer import process_sample_proteomics_qc


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate proteomics QC guardrails.")
    parser.add_argument("sample_id")
    parser.add_argument("--sample-dir", required=True)
    args = parser.parse_args(argv)
    qc_path = process_sample_proteomics_qc(Path(args.sample_dir), args.sample_id)
    print(f"Wrote {qc_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
