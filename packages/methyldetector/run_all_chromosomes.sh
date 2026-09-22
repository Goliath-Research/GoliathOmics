#!/bin/bash
#
# Run MethylDetector for all chromosomes (4-22, X) and all contexts (CG, CHG, CHH)
#

set -e  # Exit on error

# Navigate to methyldetector directory
cd "$(dirname "$0")"

# Define chromosomes and contexts
CHROMOSOMES=(4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 X)
CONTEXTS=(CG CHG CHH)

# Log file
LOG_FILE="run_all_$(date +%Y%m%d_%H%M%S).log"

echo "Starting MethylDetector analysis for chromosomes 4-22 and X" | tee -a "$LOG_FILE"
echo "Log file: $LOG_FILE" | tee -a "$LOG_FILE"
echo "Started at: $(date)" | tee -a "$LOG_FILE"
echo "======================================================" | tee -a "$LOG_FILE"

# Counter for tracking progress
TOTAL=$((${#CHROMOSOMES[@]} * ${#CONTEXTS[@]}))
CURRENT=0
FAILED=0

# Loop through all chromosomes and contexts
for CHROM in "${CHROMOSOMES[@]}"; do
  for CTX in "${CONTEXTS[@]}"; do
    CURRENT=$((CURRENT + 1))
    CONFIG="configs/pb-hc12-${CHROM}-${CTX}_config.json"
    
    echo "" | tee -a "$LOG_FILE"
    echo "[$CURRENT/$TOTAL] Processing: Chromosome $CHROM, Context $CTX" | tee -a "$LOG_FILE"
    echo "Config: $CONFIG" | tee -a "$LOG_FILE"
    echo "Started at: $(date)" | tee -a "$LOG_FILE"
    
    if [ ! -f "$CONFIG" ]; then
      echo "ERROR: Config file not found: $CONFIG" | tee -a "$LOG_FILE"
      FAILED=$((FAILED + 1))
      continue
    fi
    
    # Run MethylDetector (md script handles container execution)
    if ./md "$CONFIG" >> "$LOG_FILE" 2>&1; then
      echo "✅ SUCCESS: Chromosome $CHROM, Context $CTX" | tee -a "$LOG_FILE"
    else
      echo "❌ FAILED: Chromosome $CHROM, Context $CTX (exit code: $?)" | tee -a "$LOG_FILE"
      FAILED=$((FAILED + 1))
    fi
    
    echo "Completed at: $(date)" | tee -a "$LOG_FILE"
    echo "------------------------------------------------------" | tee -a "$LOG_FILE"
  done
done

# Summary
echo "" | tee -a "$LOG_FILE"
echo "======================================================" | tee -a "$LOG_FILE"
echo "ANALYSIS COMPLETE" | tee -a "$LOG_FILE"
echo "Finished at: $(date)" | tee -a "$LOG_FILE"
echo "Total processed: $CURRENT" | tee -a "$LOG_FILE"
echo "Successful: $((CURRENT - FAILED))" | tee -a "$LOG_FILE"
echo "Failed: $FAILED" | tee -a "$LOG_FILE"
echo "Log file: $LOG_FILE" | tee -a "$LOG_FILE"
echo "======================================================" | tee -a "$LOG_FILE"

if [ $FAILED -eq 0 ]; then
  echo "🎉 All analyses completed successfully!"
  exit 0
else
  echo "⚠️  Some analyses failed. Check $LOG_FILE for details."
  exit 1
fi

