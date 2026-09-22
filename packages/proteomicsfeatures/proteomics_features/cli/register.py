"""CLI: methyl-register-abundance SAMPLE_ID --sample-dir DIR [--source diann|panel]."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize proteomics quant output to abundance.h5.")
    parser.add_argument("sample_id")
    parser.add_argument("--sample-dir", required=True)
    parser.add_argument("--source", default="diann", choices=["diann", "panel"])
    parser.add_argument("--panel-path", default=None)
    parser.add_argument("--panel-format", default="open")
    args = parser.parse_args(argv)

    from proteomics_features.ingest import register_sample_abundance

    result = register_sample_abundance(
        sample_dir=args.sample_dir,
        sample_id=args.sample_id,
        source=args.source,
        panel_path=args.panel_path,
        panel_format=args.panel_format,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
