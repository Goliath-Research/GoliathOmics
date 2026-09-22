#!/usr/bin/env python3
"""
GPU Memory Manager

Provides GPU memory monitoring and cleanup utilities for MethylCentroid tasks.
"""

import subprocess
import json
import time
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class GPUMemoryManager:
    def __init__(self, container_name: str = "goliath"):
        self.container_name = container_name
        
    def get_gpu_memory_info(self) -> Dict[str, Any]:
        """Get current GPU memory usage."""
        try:
            # Get GPU memory info from nvidia-smi
            result = subprocess.run([
                "nvidia-smi", "--query-gpu=memory.used,memory.total,memory.free", 
                "--format=csv,noheader,nounits"
            ], capture_output=True, text=True, check=True)
            
            lines = result.stdout.strip().split('\n')
            gpu_info = []
            
            for line in lines:
                used, total, free = map(int, line.split(', '))
                gpu_info.append({
                    "used_mb": used,
                    "total_mb": total,
                    "free_mb": free,
                    "usage_percent": (used / total) * 100
                })
            
            return {"gpus": gpu_info}
            
        except Exception as e:
            logger.error(f"Failed to get GPU memory info: {e}")
            return {"gpus": []}
    
    def cleanup_gpu_memory(self) -> bool:
        """Clean up GPU memory in the container."""
        try:
            logger.info("Cleaning up GPU memory...")
            
            # Run GPU memory cleanup commands in container
            cleanup_commands = [
                ["python", "-c", "import cupy as cp; cp.get_default_memory_pool().free_all_blocks()"],
                ["python", "-c", "import gc; gc.collect()"],
                ["python", "-c", "import torch; torch.cuda.empty_cache() if torch.cuda.is_available() else None"]
            ]
            
            for cmd in cleanup_commands:
                try:
                    subprocess.run([
                        "docker", "exec", self.container_name
                    ] + cmd, capture_output=True, text=True, timeout=30)
                except subprocess.TimeoutExpired:
                    logger.warning(f"Cleanup command timed out: {' '.join(cmd)}")
                except Exception as e:
                    logger.warning(f"Cleanup command failed: {e}")
            
            # Wait a moment for cleanup to take effect
            time.sleep(2)
            
            # Check memory after cleanup
            memory_info = self.get_gpu_memory_info()
            if memory_info["gpus"]:
                gpu = memory_info["gpus"][0]
                logger.info(f"GPU memory after cleanup: {gpu['used_mb']}MB used, {gpu['free_mb']}MB free")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to cleanup GPU memory: {e}")
            return False
    
    def is_memory_pressure_high(self, threshold_percent: float = 80.0) -> bool:
        """Check if GPU memory pressure is high."""
        memory_info = self.get_gpu_memory_info()
        if not memory_info["gpus"]:
            return False
        
        gpu = memory_info["gpus"][0]
        return gpu["usage_percent"] > threshold_percent
    
    def monitor_memory_during_task(self, command: list, 
                                 max_memory_percent: float = 90.0) -> Dict[str, Any]:
        """Run a task while monitoring GPU memory usage."""
        logger.info(f"Starting task with memory monitoring: {' '.join(command)}")
        
        # Get initial memory state
        initial_memory = self.get_gpu_memory_info()
        
        # Start the task
        start_time = time.time()
        process = subprocess.Popen([
            "docker", "exec", "-w", "/home/ubuntu/MethylCentroid", 
            self.container_name
        ] + command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        # Monitor memory during execution
        max_memory_used = 0
        memory_samples = []
        
        while process.poll() is None:
            memory_info = self.get_gpu_memory_info()
            if memory_info["gpus"]:
                gpu = memory_info["gpus"][0]
                max_memory_used = max(max_memory_used, gpu["used_mb"])
                memory_samples.append({
                    "timestamp": time.time(),
                    "used_mb": gpu["used_mb"],
                    "usage_percent": gpu["usage_percent"]
                })
                
                # Check if memory usage is too high
                if gpu["usage_percent"] > max_memory_percent:
                    logger.warning(f"High memory usage detected: {gpu['usage_percent']:.1f}%")
            
            time.sleep(1)  # Sample every second
        
        # Get final result
        stdout, stderr = process.communicate()
        end_time = time.time()
        
        final_memory = self.get_gpu_memory_info()
        
        return {
            "command": command,
            "returncode": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "success": process.returncode == 0,
            "duration_seconds": end_time - start_time,
            "initial_memory": initial_memory,
            "final_memory": final_memory,
            "max_memory_used_mb": max_memory_used,
            "memory_samples": memory_samples
        }

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="GPU Memory Manager")
    parser.add_argument("--action", choices=["info", "cleanup", "monitor"], required=True)
    parser.add_argument("--command", nargs="+", help="Command to monitor (for monitor action)")
    parser.add_argument("--container", default="goliath", help="Container name")
    parser.add_argument("--threshold", type=float, default=80.0, help="Memory pressure threshold")
    
    args = parser.parse_args()
    
    manager = GPUMemoryManager(container_name=args.container)
    
    if args.action == "info":
        memory_info = manager.get_gpu_memory_info()
        print(json.dumps(memory_info, indent=2))
        
    elif args.action == "cleanup":
        success = manager.cleanup_gpu_memory()
        print(f"Cleanup {'successful' if success else 'failed'}")
        
    elif args.action == "monitor":
        if not args.command:
            print("Error: --command required for monitor action")
            return
        
        result = manager.monitor_memory_during_task(args.command)
        print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
