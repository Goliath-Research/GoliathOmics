"""Train ECDF classifier pickle from dmp_select classifier CSV."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import List, Optional

from ..core.ecdf_train import train_ecdf_pickle_from_csv
from ..project_resolver import resolve_classifier_config


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Train ECDF classifier pickle from DMP panel CSV")
    parser.add_argument("--project", "-p", type=Path, help="Pipeline project JSON")
    parser.add_argument("--classifier-csv", type=Path, help="dmps-{chr}-classifier.csv path")
    parser.add_argument("--chromosome", "-c", type=str, default=None)
    parser.add_argument("--group", type=str, default=None, help="Comparison label")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    if args.project is None:
        parser.error("--project is required")
    if args.group:
        from ..project_resolver import resolve_classifier_config_per_cancer_group

        pairs = resolve_classifier_config_per_cancer_group(args.project)
        matches = [(cfg, label) for cfg, label in pairs if label == args.group]
        if not matches:
            available = [label for _, label in pairs]
            parser.error(
                f"Unknown comparison group {args.group!r}; "
                f"available: {available or '(none)'}"
            )
        cfg = matches[0][0]
    else:
        cfg = resolve_classifier_config(args.project)
    chrom = args.chromosome
    if chrom is None:
        chrom_val = getattr(cfg, "chromosome", "1")
        chrom = str(chrom_val[0] if isinstance(chrom_val, list) else chrom_val)
    contexts = list(cfg.contexts or ["CG"])
    detection_dir = Path(cfg.detection_dir or cfg.output_dir or ".")
    csv_path = args.classifier_csv or (detection_dir / f"dmps-{chrom}-classifier.csv")
    out_dir = args.output_dir or detection_dir
    temp = float(args.temperature if args.temperature is not None else getattr(cfg, "temperature", 1.0))
    try:
        path = train_ecdf_pickle_from_csv(
            csv_path,
            chromosome=str(chrom),
            contexts=contexts,
            centroid1_dir=cfg.centroid1_dir,
            centroid2_dir=cfg.centroid2_dir,
            output_dir=out_dir,
            temperature=temp,
            enable_platt_calibration=bool(getattr(cfg, "enable_platt_calibration", False)),
        )
        print(f"Wrote {path}")
    except Exception as exc:
        logging.exception("Classifier train failed")
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
