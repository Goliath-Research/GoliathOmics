#!/bin/bash

# Multiple comparison command for MethylDetector
# This script runs comparisons for all chromosome-context combinations

python -m methyl_detector \
    --centroid1-dir /home/ubuntu/Work/output_workflows/arabidopsis/centroids/WT \
    --centroid2-dir /home/ubuntu/Work/output_workflows/arabidopsis/centroids/msh1 \
    --chromosomes 1,2,3,4,5 \
    --contexts CG,CHG,CHH \
    --alpha 0.05 \
    --output-dir /home/ubuntu/Work/output_workflows/arabidopsis/WT-msh1-multiple \
    --min-n 10 \
    --global-significance-threshold 0.05
