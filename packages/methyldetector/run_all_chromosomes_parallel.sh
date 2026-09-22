#!/bin/bash
#
# Run MethylDetector for all chromosomes (4-22, X) and all contexts (CG, CHG, CHH) in parallel
#

set -e  # Exit on error

# Navigate to methyldetector directory
cd "$(dirname "$0")"

# Define chromosomes and contexts
CHROMOSOMES=(1 2 34 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 X)
CONTEXTS=(CG CHG CHH)

# Maximum number of parallel jobs (adjust based on your GPU memory)
MAX_JOBS=${1:-3}  # Default to 3 parallel jobs, can be overridden by first argument

# Log directory
LOG_DIR="logs_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOG_DIR"

MAIN_LOG="${LOG_DIR}/main.log"

echo "Starting MethylDetector analysis for chromosomes 1-22 and X" | tee "$MAIN_LOG"
echo "Running with up to $MAX_JOBS parallel jobs" | tee -a "$MAIN_LOG"
echo "Log directory: $LOG_DIR" | tee -a "$MAIN_LOG"
echo "Started at: $(date)" | tee -a "$MAIN_LOG"
echo "======================================================" | tee -a "$MAIN_LOG"

# Function to run a single analysis
run_analysis() {
  local CHROM=$1
  local CTX=$2
  local CONFIG="configs/pb-hc34-${CHROM}-${CTX}_config.json"
  local LOG_FILE="${LOG_DIR}/${CHROM}-${CTX}.log"
  
  echo "[$(date)] Starting: Chromosome $CHROM, Context $CTX" > "$LOG_FILE"
  
  if [ ! -f "$CONFIG" ]; then
    echo "[$(date)] ERROR: Config file not found: $CONFIG" >> "$LOG_FILE"
    echo "❌ FAILED: $CHROM-$CTX (config not found)" >> "$MAIN_LOG"
    return 1
  fi
  
  # Run MethylDetector (modeler wrapper or python -m methyl_detector)
  if (cd "$(dirname "$0")" && python -m methyl_detector.cli "$CONFIG") >> "$LOG_FILE" 2>&1; then
    echo "[$(date)] ✅ SUCCESS: Chromosome $CHROM, Context $CTX" >> "$LOG_FILE"
    echo "✅ SUCCESS: $CHROM-$CTX" >> "$MAIN_LOG"
    return 0
  else
    local EXIT_CODE=$?
    echo "[$(date)] ❌ FAILED: Chromosome $CHROM, Context $CTX (exit code: $EXIT_CODE)" >> "$LOG_FILE"
    echo "❌ FAILED: $CHROM-$CTX (exit code: $EXIT_CODE)" >> "$MAIN_LOG"
    return 1
  fi
}

export -f run_analysis
export LOG_DIR
export MAIN_LOG

# Counter
TOTAL=$((${#CHROMOSOMES[@]} * ${#CONTEXTS[@]}))
echo "Total analyses to run: $TOTAL" | tee -a "$MAIN_LOG"
echo "" | tee -a "$MAIN_LOG"

# Run all analyses in parallel using GNU parallel or xargs
if command -v parallel &> /dev/null; then
  # Use GNU parallel if available (better load balancing)
  echo "Using GNU parallel with $MAX_JOBS jobs" | tee -a "$MAIN_LOG"
  
  # Generate all combinations and run in parallel
  for CHROM in "${CHROMOSOMES[@]}"; do
    for CTX in "${CONTEXTS[@]}"; do
      echo "$CHROM $CTX"
    done
  done | parallel -j "$MAX_JOBS" --colsep ' ' run_analysis {1} {2}
  
else
  # Fallback to simple background jobs with job control
  echo "Using background jobs with $MAX_JOBS concurrent limit" | tee -a "$MAIN_LOG"
  
  for CHROM in "${CHROMOSOMES[@]}"; do
    for CTX in "${CONTEXTS[@]}"; do
      # Wait if we've reached the maximum number of jobs
      while [ $(jobs -r | wc -l) -ge "$MAX_JOBS" ]; do
        sleep 1
      done
      
      # Start the job in background
      run_analysis "$CHROM" "$CTX" &
    done
  done
  
  # Wait for all background jobs to complete
  echo "Waiting for all jobs to complete..." | tee -a "$MAIN_LOG"
  wait
fi

# Summary
SUCCESSFUL=$(grep -c "✅ SUCCESS" "$MAIN_LOG" || true)
FAILED=$(grep -c "❌ FAILED" "$MAIN_LOG" || true)

echo "" | tee -a "$MAIN_LOG"
echo "======================================================" | tee -a "$MAIN_LOG"
echo "ANALYSIS COMPLETE" | tee -a "$MAIN_LOG"
echo "Finished at: $(date)" | tee -a "$MAIN_LOG"
echo "Total processed: $TOTAL" | tee -a "$MAIN_LOG"
echo "Successful: $SUCCESSFUL" | tee -a "$MAIN_LOG"
echo "Failed: $FAILED" | tee -a "$MAIN_LOG"
echo "Log directory: $LOG_DIR" | tee -a "$MAIN_LOG"
echo "======================================================" | tee -a "$MAIN_LOG"

if [ $FAILED -eq 0 ]; then
  echo "🎉 All analyses completed successfully!" | tee -a "$MAIN_LOG"
  exit 0
else
  echo "⚠️  Some analyses failed. Check logs in $LOG_DIR for details." | tee -a "$MAIN_LOG"
  exit 1
fi

