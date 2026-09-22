"""CLI: methyl-rna-register-expression SAMPLE_ID --sample-dir DIR [--tx2gene PATH]."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize RNA quantifier output to expression.h5.")
    parser.add_argument("sample_id")
    parser.add_argument("--sample-dir", required=True)
    parser.add_argument("--tx2gene", default=None)
    args = parser.parse_args(argv)

    from rna_express.core.expression_store import register_sample_expression

    result = register_sample_expression(
        sample_dir=args.sample_dir,
        sample_id=args.sample_id,
        tx2gene_path=args.tx2gene,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
