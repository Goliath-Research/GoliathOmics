#!/usr/bin/env python3
"""
Build a native multiclass histogram classifier from centroids and a merged DMP table.
"""

import argparse
from pathlib import Path

from methyl_classifier.utils.multiclass_builder import build_multiclass_model_from_json


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a native multiclass histogram classifier model package"
    )
    parser.add_argument(
        "config",
        type=str,
        help="Path to multiclass model config JSON",
    )
    args = parser.parse_args()

    out_path = build_multiclass_model_from_json(Path(args.config))
    print(f"✅ Multi-class model saved to {out_path}")


if __name__ == "__main__":
    main()

