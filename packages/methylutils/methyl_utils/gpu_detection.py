"""
Centralized GPU detection and management module.

This module provides a single source of truth for GPU availability and capabilities
across the entire application ecosystem. Uses CuPy for primary detection and optional
NVML telemetry via nvidia-ml-py when enabled.
"""

import gc
import logging
import os
import time
import warnings
from typing import Any, Dict, Optional, Tuple

import numpy as np

# Initialize logger
logger = logging.getLogger(__name__)

# Global GPU state
_GPU_STATE: Optional[Dict[str, Any]] = None
_GPU_INITIALIZED: bool = False

def _env_truthy(value: Optional[str]) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}

def _should_use_nvml() -> bool:
    return _env_truthy(os.getenv("METHYLPIPELINE_ENABLE_NVML")) or _env_truthy(
        os.getenv("METHYLPIPELINE_USE_NVML")
    )

def _is_nvml_not_supported(err: Exception) -> bool:
    return err.__class__.__name__ == "NVMLError_NotSupported" or "not supported" in str(err).lower()

def _initialize_gpu_state() -> Dict[str, Any]:
    """
    Initialize and test GPU capabilities using CuPy as the primary method,
    with optional NVML telemetry when enabled.

    Returns:
        Dictionary containing GPU state information
    """
    global _GPU_STATE, _GPU_INITIALIZED
    
    if _GPU_INITIALIZED:
        return _GPU_STATE
    
    gpu_state = {
        'available': False,
        'cupy_available': False,
        'cudf_available': False,
        'cupyx_scipy_available': False,
        'cupyx_scipy_integrate_available': False,
        'cupyx_scipy_special_available': False,
        'cupyx_scipy_stats_available': False,
        'pynvml_available': False,
        'memory_gb': 0.0,
        'device_count': 0,
        'gpu_name': None,
        'cuda_version': None,
        'compute_capability': None,
        'error_message': None
    }
    
    # Primary detection: CuPy (compute availability)
    try:
        import cupy as cp
        gpu_state['cupy_available'] = True

        # Test if GPU is actually available and functional
        if cp.is_available():
            gpu_state['available'] = True

            # Basic info from CuPy
            try:
                gpu_state['device_count'] = cp.cuda.runtime.getDeviceCount()
            except Exception as e:
                logger.debug(f"CuPy device count failed: {e}")

            try:
                props = cp.cuda.runtime.getDeviceProperties(0)
                name = props.get('name')
                if name:
                    gpu_state['gpu_name'] = name.decode('utf-8') if isinstance(name, bytes) else name
                major = props.get('major')
                minor = props.get('minor')
                if major is not None and minor is not None:
                    gpu_state['compute_capability'] = f"{major}.{minor}"
            except Exception as e:
                logger.debug(f"CuPy device properties failed: {e}")

            try:
                gpu_state['memory_gb'] = cp.cuda.runtime.memGetInfo()[1] / (1024**3)
            except Exception as e:
                logger.debug(f"CuPy memory info failed: {e}")

            try:
                gpu_state['cuda_version'] = str(cp.cuda.runtime.driverGetVersion())
            except Exception as e:
                logger.debug(f"CuPy driver version failed: {e}")

            # Test basic GPU operations
            try:
                test_array = cp.array([1.0, 2.0, 3.0])
                result = cp.sum(test_array)
                del test_array, result
                cp.get_default_memory_pool().free_all_blocks()
                logger.info("GPU functionality test passed")
            except Exception as e:
                gpu_state['available'] = False
                gpu_state['error_message'] = f"GPU test failed: {e}"
                logger.warning(f"GPU test failed: {e}")
        else:
            gpu_state['error_message'] = "CuPy available but no GPU detected"
            logger.debug("CuPy available but no GPU detected")

    except ImportError:
        gpu_state['error_message'] = "CuPy not available"
        logger.debug("CuPy not available")
    except Exception as e:
        gpu_state['error_message'] = f"CuPy import failed: {e}"
        logger.warning(f"CuPy import failed: {e}")

    # Optional NVML telemetry (disabled by default)
    if _should_use_nvml():
        try:
            import pynvml as nvml
        except ImportError:
            logger.debug("nvidia-ml-py not available; NVML telemetry disabled")
        else:
            try:
                nvml.nvmlInit()
            except Exception as e:
                logger.debug(f"nvidia-ml-py initialization failed: {e}")
            else:
                gpu_state['pynvml_available'] = True
                try:
                    device_count = nvml.nvmlDeviceGetCount()
                except Exception as e:
                    if _is_nvml_not_supported(e):
                        logger.debug(f"nvidia-ml-py device count not supported: {e}")
                    else:
                        logger.debug(f"nvidia-ml-py device count failed: {e}")
                    device_count = 0

                if device_count and gpu_state['device_count'] == 0:
                    gpu_state['device_count'] = device_count

                if device_count > 0:
                    try:
                        handle = nvml.nvmlDeviceGetHandleByIndex(0)
                    except Exception as e:
                        if _is_nvml_not_supported(e):
                            logger.debug(f"nvidia-ml-py device handle not supported: {e}")
                        else:
                            logger.debug(f"nvidia-ml-py device handle failed: {e}")
                        handle = None

                    if handle is not None:
                        # GPU name
                        if not gpu_state['gpu_name']:
                            try:
                                gpu_name_raw = nvml.nvmlDeviceGetName(handle)
                                gpu_state['gpu_name'] = (
                                    gpu_name_raw.decode('utf-8')
                                    if isinstance(gpu_name_raw, bytes)
                                    else gpu_name_raw
                                )
                            except Exception as e:
                                if _is_nvml_not_supported(e):
                                    logger.debug(f"nvidia-ml-py device name not supported: {e}")
                                else:
                                    logger.debug(f"nvidia-ml-py device name failed: {e}")

                        # Memory info
                        if gpu_state['memory_gb'] <= 0:
                            try:
                                mem_info = nvml.nvmlDeviceGetMemoryInfo(handle)
                                gpu_state['memory_gb'] = mem_info.total / (1024**3)
                            except Exception as e:
                                if _is_nvml_not_supported(e):
                                    logger.debug(f"nvidia-ml-py memory info not supported: {e}")
                                else:
                                    logger.debug(f"nvidia-ml-py memory info failed: {e}")

                        # CUDA version
                        if not gpu_state['cuda_version']:
                            try:
                                driver_version_raw = nvml.nvmlSystemGetDriverVersion()
                                gpu_state['cuda_version'] = (
                                    driver_version_raw.decode('utf-8')
                                    if isinstance(driver_version_raw, bytes)
                                    else str(driver_version_raw)
                                )
                            except Exception as e:
                                if _is_nvml_not_supported(e):
                                    logger.debug(f"nvidia-ml-py driver version not supported: {e}")
                                else:
                                    logger.debug(f"nvidia-ml-py driver version failed: {e}")

                        # Compute capability
                        if not gpu_state['compute_capability']:
                            try:
                                major, minor = nvml.nvmlDeviceGetCudaComputeCapability(handle)
                                gpu_state['compute_capability'] = f"{major}.{minor}"
                            except Exception as e:
                                if _is_nvml_not_supported(e):
                                    logger.debug(f"nvidia-ml-py compute capability not supported: {e}")
                                else:
                                    logger.debug(f"nvidia-ml-py compute capability failed: {e}")

                        if gpu_state['gpu_name']:
                            logger.info(f"GPU detected via nvidia-ml-py: {gpu_state['gpu_name']}")
                            if gpu_state['memory_gb'] > 0:
                                logger.info(f"GPU memory: {gpu_state['memory_gb']:.1f} GB")
                            if gpu_state['cuda_version']:
                                logger.info(f"CUDA version: {gpu_state['cuda_version']}")
                            if gpu_state['compute_capability']:
                                logger.info(f"Compute capability: {gpu_state['compute_capability']}")
    
    # Test cuDF availability
    if gpu_state['cupy_available']:
        try:
            import importlib.util
            if importlib.util.find_spec("cudf") is not None:
                gpu_state['cudf_available'] = True
        except Exception as e:
            logger.warning(f"cuDF availability check failed: {e}")
    
    # Test cupyx.scipy availability
    if gpu_state['cupy_available']:
        try:
            import cupyx.scipy
            gpu_state['cupyx_scipy_available'] = True
            
            # Test specific cupyx.scipy modules
            try:
                import cupyx.scipy.integrate
                if hasattr(cupyx.scipy.integrate, 'quad'):
                    gpu_state['cupyx_scipy_integrate_available'] = True
            except ImportError:
                pass
            
            try:
                import cupyx.scipy.special
                gpu_state['cupyx_scipy_special_available'] = True
            except ImportError:
                pass
            
            try:
                import cupyx.scipy.stats
                gpu_state['cupyx_scipy_stats_available'] = True
            except ImportError:
                pass
                
        except ImportError:
            logger.debug("cupyx.scipy not available")
        except Exception as e:
            logger.warning(f"cupyx.scipy import failed: {e}")
    
    _GPU_STATE = gpu_state
    _GPU_INITIALIZED = True
    
    # Log final state
    if gpu_state['available']:
        device_note = (
            f"{gpu_state['device_count']} device(s)"
            if gpu_state['device_count'] > 0
            else "device count unknown"
        )
        memory_note = (
            f"~{gpu_state['memory_gb']:.1f}GB memory"
            if gpu_state['memory_gb'] > 0
            else "memory unknown"
        )
        logger.info(f"GPU acceleration available: {device_note}, {memory_note}")
    else:
        logger.info("GPU acceleration not available - using CPU-only mode")
        if gpu_state['error_message']:
            logger.debug(f"GPU unavailable reason: {gpu_state['error_message']}")
    
    return gpu_state

def is_gpu_available() -> bool:
    """
    Check if GPU acceleration is available.
    
    Returns:
        True if GPU is available and functional, False otherwise
    """
    state = _initialize_gpu_state()
    return state['available']

def is_cupy_available() -> bool:
    """
    Check if CuPy is available (regardless of GPU).
    
    Returns:
        True if CuPy can be imported, False otherwise
    """
    state = _initialize_gpu_state()
    return state['cupy_available']

def is_cudf_available() -> bool:
    """
    Check if cuDF is available.
    
    Returns:
        True if cuDF can be imported, False otherwise
    """
    state = _initialize_gpu_state()
    return state['cudf_available']

def is_cupyx_scipy_available() -> bool:
    """
    Check if cupyx.scipy is available.
    
    Returns:
        True if cupyx.scipy can be imported, False otherwise
    """
    state = _initialize_gpu_state()
    return state['cupyx_scipy_available']

def is_cupyx_scipy_integrate_available() -> bool:
    """
    Check if cupyx.scipy.integrate is available.
    
    Returns:
        True if cupyx.scipy.integrate can be imported, False otherwise
    """
    state = _initialize_gpu_state()
    return state['cupyx_scipy_integrate_available']

def is_cupyx_scipy_special_available() -> bool:
    """
    Check if cupyx.scipy.special is available.
    
    Returns:
        True if cupyx.scipy.special can be imported, False otherwise
    """
    state = _initialize_gpu_state()
    return state['cupyx_scipy_special_available']

def is_cupyx_scipy_stats_available() -> bool:
    """
    Check if cupyx.scipy.stats is available.
    
    Returns:
        True if cupyx.scipy.stats can be imported, False otherwise
    """
    state = _initialize_gpu_state()
    return state['cupyx_scipy_stats_available']

def get_gpu_memory_gb() -> float:
    """
    Get available GPU memory in GB.
    
    Returns:
        GPU memory in GB, or 0.0 if not available
    """
    state = _initialize_gpu_state()
    return state['memory_gb']

def get_gpu_device_count() -> int:
    """
    Get number of available GPU devices.
    
    Returns:
        Number of GPU devices, or 0 if not available
    """
    state = _initialize_gpu_state()
    return state['device_count']

def get_gpu_state() -> Dict[str, Any]:
    """
    Get complete GPU state information.
    
    Returns:
        Dictionary containing all GPU state information
    """
    return _initialize_gpu_state().copy()

def get_gpu_error_message() -> Optional[str]:
    """
    Get error message if GPU is not available.
    
    Returns:
        Error message string, or None if GPU is available
    """
    state = _initialize_gpu_state()
    return state['error_message']

def get_cupy():
    """
    Get CuPy module if available.

    Returns:
        CuPy module if available, None otherwise
    """
    if not is_gpu_available():
        return None

    try:
        import cupy as cp
        return cp
    except ImportError:
        return None

def cleanup_gpu_memory() -> bool:
    """
    Clean up GPU memory.
    
    Returns:
        True if cleanup was successful, False otherwise
    """
    if not is_cupy_available():
        return False
    
    try:
        import cupy as cp

        cleaned = False

        try:
            if hasattr(cp, "cuda"):
                cp.cuda.Device().synchronize()
                cp.cuda.runtime.deviceSynchronize()
        except Exception as e:
            logger.debug("GPU synchronize before cleanup skipped: %s", e)

        try:
            cp.get_default_memory_pool().free_all_blocks()
            cleaned = True
        except Exception as e:
            logger.debug("CuPy default memory pool cleanup skipped: %s", e)

        try:
            cp.get_default_pinned_memory_pool().free_all_blocks()
            cleaned = True
        except Exception as e:
            logger.debug("CuPy pinned memory pool cleanup skipped: %s", e)

        try:
            if hasattr(cp, "clear_memo"):
                cp.clear_memo()
        except Exception as e:
            logger.debug("CuPy memo cleanup skipped: %s", e)

        # Some environments route CuPy allocations through RMM. Free those blocks too
        # so large centroid builders do not accumulate unreleased GPU memory between samples.
        try:
            import rmm

            allocator = None
            if hasattr(rmm, "get_current_allocator"):
                allocator = rmm.get_current_allocator()
            if allocator is not None and hasattr(allocator, "free_all_blocks"):
                allocator.free_all_blocks()
                cleaned = True
        except ImportError:
            pass
        except Exception as e:
            logger.debug("RMM cleanup skipped: %s", e)

        gc.collect()
        logger.debug("GPU memory cleaned up successfully")
        return cleaned
    except Exception as e:
        logger.warning(f"GPU memory cleanup failed: {e}")
        return False

def reset_gpu_state() -> None:
    """
    Reset GPU state (useful for testing or after GPU changes).
    """
    global _GPU_STATE, _GPU_INITIALIZED
    _GPU_STATE = None
    _GPU_INITIALIZED = False
    logger.debug("GPU state reset")

def print_gpu_status() -> None:
    """
    Print detailed GPU status information.
    """
    state = get_gpu_state()
    
    print("=" * 50)
    print("GPU STATUS REPORT")
    print("=" * 50)
    
    if state['available']:
        print(f"✅ GPU Acceleration: ENABLED")
        print(f"   Device: {state['gpu_name']}")
        print(f"   Memory: {state['memory_gb']:.1f} GB")
        print(f"   CUDA Version: {state['cuda_version']}")
        print(f"   Compute Capability: {state['compute_capability']}")
        print(f"   Device Count: {state['device_count']}")
    else:
        print(f"❌ GPU Acceleration: DISABLED")
        if state['error_message']:
            print(f"   Reason: {state['error_message']}")
    
    print(f"\nLibrary Availability:")
    print(f"   CuPy: {'✅' if state['cupy_available'] else '❌'}")
    print(f"   cuDF: {'✅' if state['cudf_available'] else '❌'}")
    print(f"   cupyx.scipy: {'✅' if state['cupyx_scipy_available'] else '❌'}")
    print(f"   pynvml: {'✅' if state['pynvml_available'] else '❌'}")
    
    if state['cupyx_scipy_available']:
        print(f"\nCuPy SciPy Modules:")
        print(f"   integrate: {'✅' if state['cupyx_scipy_integrate_available'] else '❌'}")
        print(f"   special: {'✅' if state['cupyx_scipy_special_available'] else '❌'}")
        print(f"   stats: {'✅' if state['cupyx_scipy_stats_available'] else '❌'}")
    
    print("=" * 50)

def get_gpu_capabilities() -> Dict[str, Any]:
    """
    Get detailed GPU capabilities information.
    
    Returns:
        Dictionary with GPU capabilities
    """
    state = get_gpu_state()
    
    return {
        'nvidia_gpu_available': state['available'],
        'cupy_available': state['cupy_available'],
        'cudf_available': state['cudf_available'],
        'cupyx_scipy_available': state['cupyx_scipy_available'],
        'pynvml_available': state['pynvml_available'],
        'device_count': state['device_count'],
        'memory_gb': state['memory_gb'],
        'gpu_name': state['gpu_name'],
        'cuda_version': state['cuda_version'],
        'compute_capability': state['compute_capability'],
        'error_message': state['error_message']
    }

def create_gpu_array(array: np.ndarray) -> Tuple[np.ndarray, bool]:
    """
    Create GPU array if possible, otherwise return CPU array.
    
    Args:
        array: Input numpy array
        
    Returns:
        Tuple of (array, is_gpu) where is_gpu indicates if it's on GPU
    """
    if not is_gpu_available():
        return array, False
    
    try:
        cp = get_cupy()
        if cp is not None:
            gpu_array = cp.asarray(array)
            return gpu_array, True
    except Exception as e:
        logger.warning(f"Failed to create GPU array: {e}")
    
    return array, False

def to_cpu_array(array) -> np.ndarray:
    """
    Convert array to CPU numpy array.

    Args:
        array: Input array (numpy or CuPy)

    Returns:
        CPU numpy array
    """
    from .array_backend import to_cpu

    return to_cpu(array)

def get_memory_info() -> Dict[str, int]:
    """
    Get current GPU memory usage information.
    
    Returns:
        Dictionary with memory usage info
    """
    if not is_gpu_available():
        return {'gpu_memory_used': 0, 'gpu_memory_total': 0, 'gpu_memory_free': 0}
    
    try:
        cp = get_cupy()
        if cp is not None:
            mempool = cp.get_default_memory_pool()
            used_bytes = mempool.used_bytes()
            total_bytes = mempool.total_bytes()
            free_bytes = total_bytes - used_bytes
            
            return {
                'gpu_memory_used': used_bytes,
                'gpu_memory_total': total_bytes,
                'gpu_memory_free': free_bytes
            }
    except Exception as e:
        logger.warning(f"Failed to get GPU memory info: {e}")
    
    return {'gpu_memory_used': 0, 'gpu_memory_total': 0, 'gpu_memory_free': 0}

def compare_implementations(cpu_func, gpu_func, *args, **kwargs):
    """
    Compare CPU and GPU implementations side by side.
    
    Args:
        cpu_func: CPU implementation function
        gpu_func: GPU implementation function
        *args, **kwargs: Arguments to pass to both functions
        
    Returns:
        Dictionary with results and timing information
    """
    results = {
        'cpu_result': None,
        'gpu_result': None,
        'cpu_time': 0,
        'gpu_time': 0,
        'gpu_available': is_gpu_available(),
        'difference': None
    }
    
    # Run CPU implementation
    start_time = time.time()
    try:
        results['cpu_result'] = cpu_func(*args, **kwargs)
        results['cpu_time'] = time.time() - start_time
    except Exception as e:
        results['cpu_error'] = str(e)
    
    # Run GPU implementation if available
    if results['gpu_available']:
        start_time = time.time()
        try:
            results['gpu_result'] = gpu_func(*args, **kwargs)
            results['gpu_time'] = time.time() - start_time
        except Exception as e:
            results['gpu_error'] = str(e)
    
    # Calculate difference if both succeeded
    if results['cpu_result'] is not None and results['gpu_result'] is not None:
        if isinstance(results['cpu_result'], (int, float)) and isinstance(results['gpu_result'], (int, float)):
            results['difference'] = abs(results['cpu_result'] - results['gpu_result'])
        else:
            results['difference'] = "Non-numeric results"
    
    return results

# Convenience aliases for backward compatibility
GPU_AVAILABLE = is_gpu_available
RAPIDS_AVAILABLE = lambda: is_cupy_available() and is_cudf_available()

# Initialize GPU state on import
_initialize_gpu_state()
