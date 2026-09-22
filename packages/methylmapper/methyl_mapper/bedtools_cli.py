#!/usr/bin/env python3
"""
Command-line entry point for MethylMapper Bedtools mapping.

This script provides the supported local bedtools-based DMP-to-feature mapping
flow. It maps DMPs to genes and other genomic features with weighting by
statistical significance.
"""

from methyl_mapper.cli import main_bedtools

if __name__ == '__main__':
    main_bedtools()

