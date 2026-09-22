# GPU Memory Management for MethylCentroid

This guide provides solutions for managing GPU memory fragmentation and hanging memory when running MethylCentroid tasks sequentially in Docker containers.

## Problem

When running heavy GPU processing tasks sequentially in a container, you may experience:
- GPU memory fragmentation
- Hanging memory that doesn't get released
- Out of memory errors on subsequent tasks
- Degraded performance over time

## Solutions

### 1. Container Restart Strategy (Recommended)

The most reliable approach is to restart the container between heavy GPU tasks:

```bash
# Run a single task with container restart
python scripts/gpu_task_runner.py --config scripts/task_queue_example.json

# Run without container restarts (for debugging)
python scripts/gpu_task_runner.py --config scripts/task_queue_example.json --no-restart
```

**Advantages:**
- Complete memory cleanup
- No fragmentation issues
- Reliable and predictable
- Works with any GPU workload

**Disadvantages:**
- Slight overhead for container restart
- Need to wait for container to be ready

### 2. Memory Monitoring and Cleanup

Monitor GPU memory usage and perform cleanup when needed:

```bash
# Check current GPU memory usage
python scripts/gpu_memory_manager.py --action info

# Clean up GPU memory
python scripts/gpu_memory_manager.py --action cleanup

# Monitor memory during task execution
python scripts/gpu_memory_manager.py --action monitor --command python -m methyl_centroid.centroid_cli --config packages/methylcentroid/configs/bc-cancer_config.json
```

### 3. Enhanced Memory Management in Code

Use the GPU memory utilities in your Python code:

```python
from scripts.gpu_memory_utils import GPUMemoryUtils

# Check memory pressure
if GPUMemoryUtils.is_memory_pressure_high(threshold_percent=85.0):
    print("High memory pressure detected, cleaning up...")
    GPUMemoryUtils.cleanup_gpu_memory(aggressive=True)

# Use memory context manager
with GPUMemoryUtils.create_memory_context(cleanup_after=True):
    # Your GPU operations here
    result = mc.build_centroid()

# Monitor memory during function execution
@GPUMemoryUtils.monitor_memory_usage
def process_large_dataset(data):
    # GPU operations here
    return result
```

### 4. Task Queue Configuration

Create a JSON configuration file for your task queue:

```json
{
  "tasks": [
    {
      "name": "bc-cancer-chr1-cg",
      "command": ["python", "-m", "methyl_centroid.centroid_cli", "--config", "packages/methylcentroid/configs/bc-cancer_config.json", "--chromosome", "1", "--context", "CG"],
      "requires_gpu": true,
      "priority": 1,
      "estimated_memory_gb": 4,
      "delay_seconds": 5
    }
  ],
  "settings": {
    "max_concurrent_gpu_tasks": 1,
    "memory_cleanup_threshold_percent": 85.0,
    "container_restart_interval": 5,
    "timeout_seconds": 3600
  }
}
```

## Best Practices

### 1. Choose the Right Strategy

- **Container Restart**: Use for production workloads where reliability is critical
- **Memory Cleanup**: Use for development and testing where restart overhead is not acceptable
- **Hybrid Approach**: Use memory cleanup for light tasks, container restart for heavy tasks

### 2. Monitor Memory Usage

```bash
# Continuous monitoring
python scripts/gpu_memory_utils.py --action monitor --threshold 80

# Check memory before/after tasks
python scripts/gpu_memory_utils.py --action info
```

### 3. Configure Memory Thresholds

Set appropriate thresholds based on your GPU memory:
- **80%**: Warning threshold for memory cleanup
- **90%**: Critical threshold for aggressive cleanup
- **95%**: Emergency threshold for container restart

### 4. Use Memory Context Managers

Always use context managers for GPU operations:

```python
with GPUMemoryUtils.create_memory_context(cleanup_after=True):
    # GPU operations
    result = mc.build_centroid()
```

### 5. Batch Similar Tasks

Group tasks by memory requirements to minimize cleanup overhead:

```python
# Light tasks (no restart needed)
light_tasks = [task for task in tasks if task["estimated_memory_gb"] < 2]

# Heavy tasks (restart needed)
heavy_tasks = [task for task in tasks if task["estimated_memory_gb"] >= 2]
```

## Troubleshooting

### Common Issues

1. **Out of Memory Errors**
   - Increase memory cleanup frequency
   - Use container restart strategy
   - Reduce batch sizes

2. **Slow Performance**
   - Check for memory fragmentation
   - Use aggressive cleanup
   - Consider container restart

3. **Memory Not Released**
   - Use aggressive cleanup mode
   - Check for memory leaks in code
   - Restart container

### Debug Commands

```bash
# Check GPU memory usage
nvidia-smi

# Monitor memory in container
docker exec goliath python scripts/gpu_memory_utils.py --action info

# Clean up memory
docker exec goliath python scripts/gpu_memory_utils.py --action cleanup --aggressive

# Monitor during task execution
docker exec goliath python scripts/gpu_memory_manager.py --action monitor --command "python -m methyl_centroid.centroid_cli --config packages/methylcentroid/configs/bc-cancer_config.json"
```

## Performance Impact

| Strategy | Memory Cleanup | Performance Impact | Reliability |
|----------|----------------|-------------------|-------------|
| Container Restart | Complete | 10-30s overhead | High |
| Memory Cleanup | Good | 1-5s overhead | Medium |
| Hybrid | Variable | 1-30s overhead | High |

## Integration with Existing Code

The memory management utilities are designed to work with your existing MethylCentroid code without modifications. Simply wrap your existing commands:

```bash
# Instead of:
docker exec -w /home/ubuntu/MethylCentroid goliath python -m methyl_centroid.centroid_cli --config packages/methylcentroid/configs/bc-cancer_config.json

# Use:
python scripts/gpu_task_runner.py --config scripts/task_queue_example.json
```

This provides automatic memory management while maintaining the same functionality.
