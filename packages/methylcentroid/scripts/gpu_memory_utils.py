#!/usr/bin/env python3
"""
GPU Memory Utilities for MethylCentroid

Provides comprehensive GPU memory management for long-running containers.
"""

import gc
import logging
import time
from typing import Dict, Any

logger = logging.getLogger(__name__)

class GPUMemoryUtils:
    """Utility class for GPU memory management."""
    
    @staticmethod
    def cleanup_gpu_memory(aggressive: bool = False) -> Dict[str, Any]:
        """
        Comprehensive GPU memory cleanup.
        
        Args:
            aggressive: If True, performs more aggressive cleanup
            
        Returns:
            Dictionary with cleanup results
        """
        cleanup_results = {
            "cupy_cleanup": False,
            "torch_cleanup": False,
            "gc_cleanup": False,
            "memory_freed_mb": 0
        }
        
        try:
            # Get initial memory state
            initial_memory = GPUMemoryUtils.get_gpu_memory_usage()
            
            # CuPy cleanup
            try:
                import cupy as cp
                if cp is not None:
                    # Free all memory pools
                    cp.get_default_memory_pool().free_all_blocks()
                    cp.get_default_pinned_memory_pool().free_all_blocks()
                    
                    if aggressive:
                        # Force garbage collection on GPU
                        cp.cuda.runtime.deviceSynchronize()
                        cp.cuda.runtime.deviceReset()
                    
                    cleanup_results["cupy_cleanup"] = True
                    logger.info("CuPy memory cleanup completed")
            except ImportError:
                logger.debug("CuPy not available")
            except Exception as e:
                logger.warning(f"CuPy cleanup failed: {e}")
            
            # PyTorch cleanup
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    if aggressive:
                        torch.cuda.synchronize()
                        torch.cuda.ipc_collect()
                    
                    cleanup_results["torch_cleanup"] = True
                    logger.info("PyTorch memory cleanup completed")
            except ImportError:
                logger.debug("PyTorch not available")
            except Exception as e:
                logger.warning(f"PyTorch cleanup failed: {e}")
            
            # Python garbage collection
            try:
                collected = gc.collect()
                cleanup_results["gc_cleanup"] = True
                logger.info(f"Python GC collected {collected} objects")
            except Exception as e:
                logger.warning(f"Python GC failed: {e}")
            
            # Get final memory state
            final_memory = GPUMemoryUtils.get_gpu_memory_usage()
            cleanup_results["memory_freed_mb"] = initial_memory.get("used_mb", 0) - final_memory.get("used_mb", 0)
            
            logger.info(f"GPU memory cleanup completed. Freed: {cleanup_results['memory_freed_mb']}MB")
            
        except Exception as e:
            logger.error(f"GPU memory cleanup failed: {e}")
        
        return cleanup_results
    
    @staticmethod
    def get_gpu_memory_usage() -> Dict[str, Any]:
        """Get current GPU memory usage."""
        try:
            import cupy as cp
            if cp is not None:
                mempool = cp.get_default_memory_pool()
                pinned_mempool = cp.get_default_pinned_memory_pool()
                
                # Get basic memory pool info
                used_mb = mempool.used_bytes() / (1024 * 1024)
                total_mb = mempool.total_bytes() / (1024 * 1024)
                free_mb = (total_mb - used_mb)
                
                # Pinned memory pool info is limited in CuPy
                # We can only get the number of free blocks, not total/used bytes
                pinned_free_blocks = pinned_mempool.n_free_blocks()
                
                return {
                    "used_mb": used_mb,
                    "total_mb": total_mb,
                    "free_mb": free_mb,
                    "pinned_used_mb": 0,  # Not available in CuPy
                    "pinned_total_mb": 0,  # Not available in CuPy
                    "pinned_free_blocks": pinned_free_blocks
                }
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"Failed to get GPU memory usage: {e}")
        
        return {"used_mb": 0, "total_mb": 0, "free_mb": 0, "pinned_used_mb": 0, "pinned_total_mb": 0, "pinned_free_blocks": 0}
    
    @staticmethod
    def is_memory_pressure_high(threshold_percent: float = 80.0) -> bool:
        """Check if GPU memory pressure is high."""
        memory_info = GPUMemoryUtils.get_gpu_memory_usage()
        if memory_info["total_mb"] == 0:
            return False
        
        usage_percent = (memory_info["used_mb"] / memory_info["total_mb"]) * 100
        return usage_percent > threshold_percent
    
    @staticmethod
    def create_memory_context(cleanup_after: bool = True, 
                            aggressive_cleanup: bool = False):
        """
        Context manager for GPU memory management.
        
        Usage:
            with GPUMemoryUtils.create_memory_context():
                # GPU operations here
                pass
            # Memory is automatically cleaned up
        """
        class MemoryContext:
            def __init__(self, cleanup_after, aggressive_cleanup):
                self.cleanup_after = cleanup_after
                self.aggressive_cleanup = aggressive_cleanup
                self.initial_memory = None
            
            def __enter__(self):
                self.initial_memory = GPUMemoryUtils.get_gpu_memory_usage()
                logger.debug(f"Memory context started. Initial usage: {self.initial_memory['used_mb']:.1f}MB")
                return self
            
            def __exit__(self, exc_type, exc_val, exc_tb):
                if self.cleanup_after:
                    cleanup_results = GPUMemoryUtils.cleanup_gpu_memory(
                        aggressive=self.aggressive_cleanup
                    )
                    logger.debug(f"Memory context cleanup: {cleanup_results['memory_freed_mb']:.1f}MB freed")
        
        return MemoryContext(cleanup_after, aggressive_cleanup)
    
    @staticmethod
    def monitor_memory_usage(func, *args, **kwargs):
        """
        Decorator to monitor memory usage during function execution.
        
        Usage:
            @GPUMemoryUtils.monitor_memory_usage
            def my_gpu_function(data):
                # GPU operations here
                return result
        """
        def wrapper(*args, **kwargs):
            initial_memory = GPUMemoryUtils.get_gpu_memory_usage()
            logger.info(f"Function {func.__name__} started. Memory: {initial_memory['used_mb']:.1f}MB")
            
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                
                final_memory = GPUMemoryUtils.get_gpu_memory_usage()
                duration = time.time() - start_time
                
                logger.info(f"Function {func.__name__} completed in {duration:.2f}s. "
                          f"Memory: {initial_memory['used_mb']:.1f}MB -> {final_memory['used_mb']:.1f}MB "
                          f"(Δ{final_memory['used_mb'] - initial_memory['used_mb']:+.1f}MB)")
                
                return result
                
            except Exception as e:
                final_memory = GPUMemoryUtils.get_gpu_memory_usage()
                logger.error(f"Function {func.__name__} failed. Memory: {initial_memory['used_mb']:.1f}MB -> {final_memory['used_mb']:.1f}MB")
                raise
        
        return wrapper

def main():
    """CLI for GPU memory utilities."""
    import argparse
    
    parser = argparse.ArgumentParser(description="GPU Memory Utilities")
    parser.add_argument("--action", choices=["info", "cleanup", "monitor"], required=True)
    parser.add_argument("--aggressive", action="store_true", help="Use aggressive cleanup")
    parser.add_argument("--threshold", type=float, default=80.0, help="Memory pressure threshold")
    
    args = parser.parse_args()
    
    if args.action == "info":
        memory_info = GPUMemoryUtils.get_gpu_memory_usage()
        print(f"GPU Memory Usage:")
        print(f"  Used: {memory_info['used_mb']:.1f}MB")
        print(f"  Total: {memory_info['total_mb']:.1f}MB")
        print(f"  Free: {memory_info['free_mb']:.1f}MB")
        if memory_info['total_mb'] > 0:
            usage_percent = (memory_info['used_mb'] / memory_info['total_mb']) * 100
            print(f"  Usage: {usage_percent:.1f}%")
            print(f"  Pressure High: {GPUMemoryUtils.is_memory_pressure_high(args.threshold)}")
    
    elif args.action == "cleanup":
        results = GPUMemoryUtils.cleanup_gpu_memory(aggressive=args.aggressive)
        print(f"Cleanup Results:")
        print(f"  CuPy: {results['cupy_cleanup']}")
        print(f"  PyTorch: {results['torch_cleanup']}")
        print(f"  Python GC: {results['gc_cleanup']}")
        print(f"  Memory Freed: {results['memory_freed_mb']:.1f}MB")
    
    elif args.action == "monitor":
        print("Starting memory monitoring...")
        while True:
            memory_info = GPUMemoryUtils.get_gpu_memory_usage()
            pressure = GPUMemoryUtils.is_memory_pressure_high(args.threshold)
            print(f"{time.strftime('%H:%M:%S')} - Used: {memory_info['used_mb']:.1f}MB, "
                  f"Pressure: {'HIGH' if pressure else 'OK'}")
            time.sleep(5)

if __name__ == "__main__":
    main()
