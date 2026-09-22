#!/bin/bash
#
# Example workflow for using the three-stage DMP export feature
#
# This script demonstrates:
# 1. Running MethylDetector to generate three-stage CSVs
# 2. Comparing the three stages
# 3. Converting to BED format for gene annotation
#

set -e  # Exit on error

# Configuration
CONFIG="/home/ubuntu/MethylPipeline/packages/methyldetector/configs/pb-hc1-1_config.json"
OUTPUT_DIR="/home/ubuntu/Work/samples/humans/psomagen/AN00026418/detection/pb-healthy-pilot-stage1"
CHROMOSOME="1"
SCRIPTS_DIR="/home/ubuntu/MethylPipeline/packages/methyldetector/scripts"

echo "=================================================================="
echo "Three-Stage DMP Export Workflow"
echo "=================================================================="
echo ""

# Step 1: Run MethylDetector
echo "Step 1: Running MethylDetector..."
echo "------------------------------------------------------------------"
python -m methyl_detector.cli.main "$CONFIG"
echo ""
echo "✓ MethylDetector complete"
echo ""

# Check that files were generated
echo "Step 2: Verifying output files..."
echo "------------------------------------------------------------------"

FILES=(
    "$OUTPUT_DIR/dmps-${CHROMOSOME}-1-biological.csv"
    "$OUTPUT_DIR/dmps-${CHROMOSOME}-2-binary-search.csv"
    "$OUTPUT_DIR/dmps-${CHROMOSOME}-3-differential-evolution.csv"
)

for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        count=$(tail -n +2 "$file" | wc -l)
        echo "✓ Found: $(basename $file) ($count DMPs)"
    else
        echo "✗ Missing: $(basename $file)"
    fi
done
echo ""

# Step 3: Compare stages
echo "Step 3: Comparing the three stages..."
echo "------------------------------------------------------------------"
python "$SCRIPTS_DIR/compare_dmp_stages.py" "$OUTPUT_DIR" "$CHROMOSOME"
echo ""

# Step 4: Convert to BED format
echo "Step 4: Converting to BED format for gene annotation..."
echo "------------------------------------------------------------------"

BED_DIR="$OUTPUT_DIR/bed_files"
mkdir -p "$BED_DIR"

for stage in 1 2 3; do
    if [ $stage -eq 1 ]; then
        suffix="1-biological"
        label="Stage 1 (Biological)"
    elif [ $stage -eq 2 ]; then
        suffix="2-binary-search"
        label="Stage 2 (Binary Search)"
    else
        suffix="3-differential-evolution"
        label="Stage 3 (DE Optimized)"
    fi
    
    csv_file="$OUTPUT_DIR/dmps-${CHROMOSOME}-${suffix}.csv"
    bed_file="$BED_DIR/stage${stage}.bed"
    
    if [ -f "$csv_file" ]; then
        python "$SCRIPTS_DIR/csv_to_bed.py" "$csv_file" "$bed_file"
        echo "✓ Created: $(basename $bed_file) - $label"
    fi
done
echo ""

# Step 5: Summary
echo "Step 5: Summary"
echo "------------------------------------------------------------------"
echo "Output directory: $OUTPUT_DIR"
echo ""
echo "Generated files:"
echo "  CSV Files:"
echo "    - dmps-${CHROMOSOME}-1-biological.csv"
echo "    - dmps-${CHROMOSOME}-2-binary-search.csv"
echo "    - dmps-${CHROMOSOME}-3-differential-evolution.csv"
echo "    - stage_comparison_summary-${CHROMOSOME}.csv"
echo ""
echo "  BED Files (for gene annotation):"
echo "    - bed_files/stage1.bed"
echo "    - bed_files/stage2.bed"
echo "    - bed_files/stage3.bed"
echo ""
echo "Next steps:"
echo "  1. Annotate BED files with genes using bedtools, HOMER, or other tools"
echo "  2. Compare gene lists to see refinement through stages"
echo "  3. Perform pathway enrichment analysis on each stage"
echo "  4. Analyze which genes are lost/gained at each optimization step"
echo ""
echo "Example gene annotation command:"
echo "  bedtools closest -a $BED_DIR/stage1.bed -b genes.bed > genes_stage1.txt"
echo ""
echo "=================================================================="
echo "Workflow complete!"
echo "=================================================================="

