# Running Tests in Container with GPU Support

This guide explains how to run the MethylFrame statistics tests inside the container to take advantage of GPU acceleration.

## Prerequisites

- Docker container `methylpipeline` is running
- GPU is available and accessible to the container
- All required packages are installed in the container

## Running Tests in Container

### Option 1: Using Docker Exec with Working Directory (Recommended)

```bash
# Set working directory and run directly
docker exec -w /workspace/packages/methylutils/methyl_utils/tests \
  methylpipeline python3 test_methyl_frame_statistics.py \
    --config example_config.json \
    --output-dir results \
    --use-gpu

# Or test with mock data first
docker exec -w /workspace/packages/methylutils/methyl_utils/tests \
  methylpipeline python3 test_methyl_frame_statistics.py \
    --mock \
    --output-dir results \
    --use-gpu \
    --chromosomes 1 2 \
    --contexts CG
```

### Option 1b: Interactive Shell (Alternative)

```bash
# Enter the container with working directory set
docker exec -it -w /home/ubuntu/MethylPipeline/packages/methylutils/methyl_utils/tests \
  methylpipeline bash

# Now you're already in the test directory
python test_methyl_frame_statistics.py --config example_config.json --output-dir results --use-gpu
```

### Option 2: Direct Docker Exec Command (Simplified)

```bash
# Using -w flag to set working directory (cleanest approach)
docker exec -w /workspace/packages/methylutils/methyl_utils/tests \
  methylpipeline python3 test_methyl_frame_statistics.py \
    --config example_config.json \
    --output-dir results \
    --use-gpu
```

### Option 3: Using Docker Run (if container not running)

```bash
# Mount the workspace and run
docker run --gpus all -it --rm \
  -v /home/ubuntu/MethylPipeline:/workspace \
  methylpipeline bash -c \
  "cd /workspace/packages/methylutils/methyl_utils/tests && \
   python test_methyl_frame_statistics.py --config example_config.json --output-dir results --use-gpu"
```

Or with original path:

```bash
docker run --gpus all -it --rm \
  -v /home/ubuntu/MethylPipeline:/home/ubuntu/MethylPipeline \
  methylpipeline bash -c \
  "cd /home/ubuntu/MethylPipeline/packages/methylutils/methyl_utils/tests && \
   python test_methyl_frame_statistics.py --config example_config.json --output-dir results --use-gpu"
```

## GPU Options

### Enable GPU Acceleration

```bash
# Use GPU if available (default: auto-detect)
python test_methyl_frame_statistics.py --config example_config.json --use-gpu

# Force CPU-only processing
python test_methyl_frame_statistics.py --config example_config.json --no-gpu
```

### Check GPU Status

The script will automatically detect and report GPU status:

```
🚀 GPU Acceleration Enabled
   GPU Devices: 1
   GPU Memory: 96.0 GB
```

If GPU is not available, it will fall back to CPU:

```
⚠️  GPU requested but not available, falling back to CPU
```

## Example Commands

### 1. Test with Mock Data (GPU)

```bash
docker exec -w /workspace/packages/methylutils/methyl_utils/tests \
  methylpipeline python3 test_methyl_frame_statistics.py \
    --mock \
    --use-gpu \
    --chromosomes 1 2 X \
    --contexts CG \
    --output-dir /tmp/test_results \
    --stats-output /tmp/test_stats.json
```

### 2. Process Real Samples (GPU)

```bash
docker exec -w /workspace/packages/methylutils/methyl_utils/tests \
  methylpipeline python3 test_methyl_frame_statistics.py \
    --config example_config.json \
    --use-gpu \
    --chromosomes 1 2 3 \
    --contexts CG CHG \
    --output-dir /tmp/results \
    --stats-output /tmp/statistics.csv
```

### 3. Run Pytest Tests

```bash
docker exec -w /workspace \
  methylpipeline pytest packages/methylutils/methyl_utils/tests/test_methyl_frame_statistics.py -v
```

## GPU Performance Benefits

When using GPU acceleration:

- **Centroid Creation**: 10-100x faster for large datasets
- **Statistics Computation**: Faster array operations
- **Memory Efficiency**: Better handling of large genomic datasets

## Troubleshooting

### Path Not Found

If you get "No such file or directory" error:

**Solution: Use `-w` flag to set working directory**

```bash
# This sets the working directory automatically
docker exec -w /workspace/packages/methylutils/methyl_utils/tests \
  methylpipeline python3 test_methyl_frame_statistics.py --mock --output-dir /tmp/results
```

If the path is different in your container:

1. **Find the correct path:**
   ```bash
   docker exec methylpipeline find / -name "test_methyl_frame_statistics.py" 2>/dev/null
   ```

2. **Use that path with -w flag:**
   ```bash
   docker exec -w /found/path/to/tests methylpipeline python test_methyl_frame_statistics.py --mock
   ```

### GPU Not Detected

If GPU is not detected:

1. Verify GPU is accessible:
   ```bash
   docker exec -it methylpipeline nvidia-smi
   ```

2. Check CUDA availability:
   ```bash
   docker exec -it methylpipeline python -c "import cupy; print(cupy.cuda.runtime.getDeviceCount())"
   ```

3. Verify container has GPU access:
   ```bash
   docker run --gpus all --rm nvidia/cuda:11.0-base nvidia-smi
   ```

### Fallback to CPU

If GPU is requested but not available, the script will automatically fall back to CPU. You'll see:

```
⚠️  GPU requested but not available, falling back to CPU
💻 Using CPU processing
```

This is normal and the tests will still run successfully.

## Output Files

Results will be saved in the specified output directory:

```
results/
├── sample_0_mC_histogram.html
├── sample_0_uC_histogram.html
├── sample_0_coverage_histogram.html
├── sample_0_methylation_level_histogram.html
└── ...
```

Statistics summary (if `--stats-output` specified):
- CSV format: `statistics.csv`
- JSON format: `statistics.json`

## Best Practices

1. **Test with Mock Data First**: Verify everything works before processing real data
2. **Monitor GPU Memory**: Large datasets may require significant GPU memory
3. **Use Appropriate Chromosomes**: Process specific chromosomes to manage memory
4. **Save Results**: Always use `--stats-output` to save statistics for later analysis

## Performance Tips

- **GPU Memory**: Ensure sufficient GPU memory for your dataset size
- **Batch Processing**: Process chromosomes separately if memory is limited
- **Context Selection**: Process CG context separately for better performance
- **Output Location**: Use `/tmp` for faster I/O in containers

