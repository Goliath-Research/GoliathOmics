# Quick Start Guide - Running Tests in Container

## Quick Command (Recommended)

Use the `-w` flag to set the working directory:

```bash
# Test with mock data (chromosome 1, CG context only - FAST)
docker exec -w /workspace/packages/methylutils/methyl_utils/tests \
  methylpipeline python3 test_methyl_frame_statistics.py \
    --mock --chromosomes 1 --contexts CG --use-gpu --output-dir /tmp/results

# Test with real data (chromosome 1, CG context only - FAST)
docker exec -w /workspace/packages/methylutils/methyl_utils/tests \
  methylpipeline python3 test_methyl_frame_statistics.py \
    --config example_config.json \
    --chromosomes 1 \
    --contexts CG \
    --use-gpu \
    --output-dir /tmp/results \
    --stats-output /tmp/statistics.csv

# Process ALL chromosomes and contexts (SLOW - only if needed)
docker exec -w /workspace/packages/methylutils/methyl_utils/tests \
  methylpipeline python3 test_methyl_frame_statistics.py \
    --config example_config.json \
    --use-gpu \
    --output-dir /tmp/results
```

## Step-by-Step

### Step 1: Enter Container (Interactive)

```bash
docker exec -it -w /home/ubuntu/MethylPipeline/packages/methylutils/methyl_utils/tests \
  methylpipeline bash
```

### Step 2: Verify Files Exist

```bash
ls -la test_methyl_frame_statistics.py example_config.json methyl_frame_stats.py
```

### Step 3: Run Tests

```bash
# Mock data test
python test_methyl_frame_statistics.py --mock --use-gpu --output-dir /tmp/results

# Real data test
python test_methyl_frame_statistics.py \
  --config example_config.json \
  --use-gpu \
  --output-dir /tmp/results \
  --stats-output /tmp/statistics.csv
```

## One-Line Commands (from host)

### Mock Data
```bash
docker exec -w /workspace/packages/methylutils/methyl_utils/tests \
  methylpipeline python3 test_methyl_frame_statistics.py --mock --use-gpu --output-dir /tmp/results
```

### Real Data
```bash
docker exec -w /workspace/packages/methylutils/methyl_utils/tests \
  methylpipeline python3 test_methyl_frame_statistics.py \
    --config example_config.json --use-gpu --output-dir /tmp/results
```

### With Specific Chromosomes/Contexts
```bash
docker exec -w /workspace/packages/methylutils/methyl_utils/tests \
  methylpipeline python3 test_methyl_frame_statistics.py \
    --config example_config.json \
    --use-gpu \
    --chromosomes 1 2 3 \
    --contexts CG \
    --output-dir /tmp/results
```

## Check Results

```bash
# In container
ls -la /tmp/results/
cat /tmp/statistics.csv  # if --stats-output was used
```

## Common Issues

**Issue**: "No such file or directory"
- **Solution**: Use `find` to locate files, or run from MethylPipeline root with relative path

**Issue**: "Module not found"
- **Solution**: Ensure you're in the container and packages are installed

**Issue**: GPU not detected
- **Solution**: Check `nvidia-smi` in container, use `--no-gpu` to force CPU

