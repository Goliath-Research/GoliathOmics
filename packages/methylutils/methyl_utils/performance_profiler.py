#!/usr/bin/env python3
"""
Performance Profiler for MethylUtils - Genome-Scale Processing Analysis

This module provides comprehensive performance profiling and monitoring
capabilities for genome-scale methylation data processing.

Key Features:
- Real-time performance monitoring
- Memory usage tracking
- GPU utilization analysis
- I/O performance metrics
- Bottleneck identification
- Performance optimization recommendations
- Historical performance data

Optimized for NVIDIA GH200 and large-scale genomic processing.
"""

import time
import psutil
import logging
from typing import Dict, List, Optional, Any, Callable, Union
from dataclasses import dataclass, field
from pathlib import Path
import json
import threading
from datetime import datetime
import numpy as np
from contextlib import contextmanager

# Container-specific imports (with fallbacks)
try:
    from .memory_manager import get_memory_manager
    MEMORY_MANAGER_AVAILABLE = True
except ImportError:
    MEMORY_MANAGER_AVAILABLE = False

try:
    from .gpu_detection import is_gpu_available, get_cupy
    GPU_AVAILABLE = True
except ImportError:
    GPU_AVAILABLE = False

logger = logging.getLogger(__name__)

@dataclass
class PerformanceMetrics:
    """Container for performance metrics."""
    timestamp: datetime = field(default_factory=datetime.now)

    # CPU metrics
    cpu_percent: float = 0.0
    cpu_count: int = 0
    memory_percent: float = 0.0
    memory_used_gb: float = 0.0
    memory_available_gb: float = 0.0

    # GPU metrics
    gpu_memory_used_gb: float = 0.0
    gpu_memory_percent: float = 0.0
    gpu_utilization_percent: float = 0.0
    gpu_temperature: float = 0.0

    # Processing metrics
    processing_rate_positions_per_sec: float = 0.0
    io_rate_mb_per_sec: float = 0.0
    chunks_processed: int = 0
    total_positions_processed: int = 0

    # System metrics
    disk_io_read_mb: float = 0.0
    disk_io_write_mb: float = 0.0
    network_io_mb: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary."""
        data = {
            'timestamp': self.timestamp.isoformat(),
            'cpu': {
                'percent': self.cpu_percent,
                'count': self.cpu_count,
                'memory_percent': self.memory_percent,
                'memory_used_gb': self.memory_used_gb,
                'memory_available_gb': self.memory_available_gb
            },
            'gpu': {
                'memory_used_gb': self.gpu_memory_used_gb,
                'memory_percent': self.gpu_memory_percent,
                'utilization_percent': self.gpu_utilization_percent,
                'temperature': self.gpu_temperature
            },
            'processing': {
                'rate_positions_per_sec': self.processing_rate_positions_per_sec,
                'io_rate_mb_per_sec': self.io_rate_mb_per_sec,
                'chunks_processed': self.chunks_processed,
                'total_positions_processed': self.total_positions_processed
            },
            'system': {
                'disk_io_read_mb': self.disk_io_read_mb,
                'disk_io_write_mb': self.disk_io_write_mb,
                'network_io_mb': self.network_io_mb
            }
        }
        return data

class PerformanceProfiler:
    """
    Advanced performance profiler for genome-scale processing.

    Provides comprehensive monitoring and analysis of system performance
    during large-scale methylation data processing.
    """

    def __init__(self,
                 enable_gpu_monitoring: bool = True,
                 enable_disk_monitoring: bool = True,
                 monitoring_interval: float = 1.0,
                 max_history_size: int = 1000):
        """
        Initialize performance profiler.

        Args:
            enable_gpu_monitoring: Whether to monitor GPU metrics
            enable_disk_monitoring: Whether to monitor disk I/O
            monitoring_interval: Time between measurements in seconds
            max_history_size: Maximum number of historical measurements to keep
        """
        self.enable_gpu_monitoring = enable_gpu_monitoring and GPU_AVAILABLE
        self.enable_disk_monitoring = enable_disk_monitoring
        self.monitoring_interval = monitoring_interval
        self.max_history_size = max_history_size

        # Performance history
        self.metrics_history: List[PerformanceMetrics] = []
        self.history_lock = threading.Lock()

        # Current operation tracking
        self.current_operation = None
        self.operation_start_time = None
        self.operation_start_metrics = None

        # Background monitoring
        self.monitoring_thread = None
        self.monitoring_active = False

        # Baseline metrics
        self.baseline_metrics = None

        logger.info("PerformanceProfiler initialized")
        logger.info(f"GPU monitoring: {self.enable_gpu_monitoring}")
        logger.info(f"Disk monitoring: {self.enable_disk_monitoring}")
        logger.info(f"Monitoring interval: {monitoring_interval}s")

    def start_monitoring(self):
        """Start background performance monitoring."""
        if self.monitoring_active:
            return

        self.monitoring_active = True
        self.monitoring_thread = threading.Thread(target=self._monitoring_worker, daemon=True)
        self.monitoring_thread.start()

        logger.info("Performance monitoring started")

    def stop_monitoring(self):
        """Stop background performance monitoring."""
        self.monitoring_active = False
        if self.monitoring_thread:
            self.monitoring_thread.join(timeout=2.0)

        logger.info("Performance monitoring stopped")

    def _monitoring_worker(self):
        """Background monitoring worker thread."""
        while self.monitoring_active:
            try:
                metrics = self._collect_metrics()
                with self.history_lock:
                    self.metrics_history.append(metrics)
                    # Maintain history size limit
                    if len(self.metrics_history) > self.max_history_size:
                        self.metrics_history = self.metrics_history[-self.max_history_size:]

            except Exception as e:
                logger.error(f"Monitoring error: {e}")

            time.sleep(self.monitoring_interval)

    def _collect_metrics(self) -> PerformanceMetrics:
        """Collect current performance metrics."""
        metrics = PerformanceMetrics()

        # CPU and memory metrics
        cpu_percent = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()

        metrics.cpu_percent = cpu_percent
        metrics.cpu_count = psutil.cpu_count()
        metrics.memory_percent = memory.percent
        metrics.memory_used_gb = memory.used / (1024**3)
        metrics.memory_available_gb = memory.available / (1024**3)

        # GPU metrics
        if self.enable_gpu_monitoring:
            try:
                cp = get_cupy()
                if cp and cp.is_available():
                    gpu_info = cp.cuda.runtime.memGetInfo()
                    gpu_used = (gpu_info[1] - gpu_info[0]) / (1024**3)

                    metrics.gpu_memory_used_gb = gpu_used
                    metrics.gpu_memory_percent = (gpu_used / 80.0) * 100  # Assuming 80GB limit

                    # Try to get utilization (may not be available)
                    try:
                        util = cp.cuda.runtime.deviceGetUtilizationRates(0)
                        metrics.gpu_utilization_percent = util[0]  # GPU utilization
                    except:
                        pass

                    # Try to get temperature
                    try:
                        temp = cp.cuda.runtime.deviceGetTemperature(0)
                        metrics.gpu_temperature = temp
                    except:
                        pass

            except Exception as e:
                logger.debug(f"GPU metrics collection failed: {e}")

        # Disk I/O metrics
        if self.enable_disk_monitoring:
            try:
                disk_io = psutil.disk_io_counters()
                if disk_io:
                    metrics.disk_io_read_mb = disk_io.read_bytes / (1024**2)
                    metrics.disk_io_write_mb = disk_io.write_bytes / (1024**2)
            except Exception as e:
                logger.debug(f"Disk I/O metrics collection failed: {e}")

        # Network I/O metrics
        try:
            net_io = psutil.net_io_counters()
            if net_io:
                metrics.network_io_mb = (net_io.bytes_sent + net_io.bytes_recv) / (1024**2)
        except Exception as e:
            logger.debug(f"Network I/O metrics collection failed: {e}")

        return metrics

    def start_operation(self, operation_name: str, operation_metadata: Optional[Dict[str, Any]] = None):
        """
        Start tracking a specific operation.

        Args:
            operation_name: Name of the operation
            operation_metadata: Additional metadata for the operation
        """
        self.current_operation = operation_name
        self.operation_start_time = time.time()
        self.operation_start_metrics = self._collect_metrics()

        logger.info(f"Started operation: {operation_name}")

    def end_operation(self) -> Dict[str, Any]:
        """
        End tracking of the current operation.

        Returns:
            Dictionary with operation performance data
        """
        if not self.current_operation or not self.operation_start_time:
            return {}

        end_time = time.time()
        duration = end_time - self.operation_start_time
        end_metrics = self._collect_metrics()

        # Calculate performance metrics
        if self.operation_start_metrics:
            # Memory usage delta
            memory_delta_gb = end_metrics.memory_used_gb - self.operation_start_metrics.memory_used_gb
            gpu_memory_delta_gb = end_metrics.gpu_memory_used_gb - self.operation_start_metrics.gpu_memory_used_gb

            # CPU usage average (simplified)
            cpu_avg = (end_metrics.cpu_percent + self.operation_start_metrics.cpu_percent) / 2

            operation_data = {
                'operation': self.current_operation,
                'duration_seconds': duration,
                'memory_delta_gb': memory_delta_gb,
                'gpu_memory_delta_gb': gpu_memory_delta_gb,
                'cpu_avg_percent': cpu_avg,
                'start_time': datetime.fromtimestamp(self.operation_start_time).isoformat(),
                'end_time': datetime.fromtimestamp(end_time).isoformat()
            }

            logger.info(f"Operation '{self.current_operation}' completed in {duration:.2f}s")
            logger.info(f"Memory delta: {memory_delta_gb:+.2f}GB, GPU: {gpu_memory_delta_gb:+.2f}GB")

            # Reset operation tracking
            self.current_operation = None
            self.operation_start_time = None
            self.operation_start_metrics = None

            return operation_data

        return {}

    @contextmanager
    def profile_operation(self, operation_name: str, operation_metadata: Optional[Dict[str, Any]] = None):
        """
        Context manager for profiling operations.

        Usage:
            with profiler.profile_operation("my_operation"):
                # Your code here
                pass

        Args:
            operation_name: Name of the operation to profile
            operation_metadata: Additional metadata for the operation
        """
        self.start_operation(operation_name, operation_metadata)
        try:
            yield
        finally:
            self.end_operation()

    def get_current_metrics(self) -> PerformanceMetrics:
        """Get current performance metrics."""
        return self._collect_metrics()

    def get_performance_summary(self, time_window_seconds: int = 60) -> Dict[str, Any]:
        """
        Get performance summary for the specified time window.

        Args:
            time_window_seconds: Time window in seconds for summary

        Returns:
            Dictionary with performance summary
        """
        with self.history_lock:
            if not self.metrics_history:
                return {}

            # Filter metrics within time window
            cutoff_time = datetime.now().timestamp() - time_window_seconds
            recent_metrics = [
                m for m in self.metrics_history
                if m.timestamp.timestamp() > cutoff_time
            ]

            if not recent_metrics:
                return {}

            # Calculate averages
            summary = {
                'time_window_seconds': time_window_seconds,
                'sample_count': len(recent_metrics),
                'cpu_avg_percent': np.mean([m.cpu_percent for m in recent_metrics]),
                'memory_avg_percent': np.mean([m.memory_percent for m in recent_metrics]),
                'memory_avg_used_gb': np.mean([m.memory_used_gb for m in recent_metrics]),
                'gpu_memory_avg_used_gb': np.mean([m.gpu_memory_used_gb for m in recent_metrics]),
                'gpu_memory_avg_percent': np.mean([m.gpu_memory_percent for m in recent_metrics]),
                'gpu_utilization_avg_percent': np.mean([m.gpu_utilization_percent for m in recent_metrics]),
                'gpu_temperature_avg': np.mean([m.gpu_temperature for m in recent_metrics if m.gpu_temperature > 0]),
                'cpu_peak_percent': max([m.cpu_percent for m in recent_metrics]),
                'memory_peak_used_gb': max([m.memory_used_gb for m in recent_metrics]),
                'gpu_memory_peak_used_gb': max([m.gpu_memory_used_gb for m in recent_metrics])
            }

            return summary

    def identify_bottlenecks(self) -> List[str]:
        """
        Identify potential performance bottlenecks.

        Returns:
            List of bottleneck descriptions
        """
        bottlenecks = []
        summary = self.get_performance_summary(300)  # Last 5 minutes

        if not summary:
            return ["No performance data available"]

        # CPU bottlenecks
        if summary.get('cpu_avg_percent', 0) > 85:
            bottlenecks.append(".1f")
        elif summary.get('cpu_avg_percent', 0) < 20:
            bottlenecks.append("Low CPU utilization - consider increasing parallelism")

        # Memory bottlenecks
        if summary.get('memory_avg_percent', 0) > 90:
            bottlenecks.append(".1f")
        if summary.get('gpu_memory_avg_percent', 0) > 85:
            bottlenecks.append(".1f")

        # GPU bottlenecks
        if summary.get('gpu_utilization_avg_percent', 0) < 50 and summary.get('gpu_memory_avg_percent', 0) < 80:
            bottlenecks.append("GPU underutilized - consider larger batch sizes")

        # Temperature warnings
        if summary.get('gpu_temperature_avg', 0) > 75:
            bottlenecks.append(".1f")

        if not bottlenecks:
            bottlenecks.append("No significant bottlenecks detected")

        return bottlenecks

    def generate_optimization_recommendations(self) -> List[str]:
        """
        Generate optimization recommendations based on performance data.

        Returns:
            List of optimization recommendations
        """
        recommendations = []
        summary = self.get_performance_summary(300)

        if not summary:
            return ["Collect more performance data for recommendations"]

        # Memory optimization
        if summary.get('memory_peak_used_gb', 0) > 300:  # Using >75% of 400GB
            recommendations.append("Consider reducing chunk sizes or implementing memory streaming")
            recommendations.append("Use memory-mapped files for large datasets")

        # GPU optimization
        if summary.get('gpu_memory_avg_percent', 0) < 60:
            recommendations.append("Increase GPU memory utilization by using larger batches")
        if summary.get('gpu_utilization_avg_percent', 0) < 70:
            recommendations.append("GPU compute underutilized - consider more parallel operations")

        # CPU optimization
        if summary.get('cpu_avg_percent', 0) > 90:
            recommendations.append("CPU utilization high - consider GPU acceleration for CPU-bound tasks")
        elif summary.get('cpu_avg_percent', 0) < 30:
            recommendations.append("CPU underutilized - consider increasing thread count or parallelism")

        # I/O optimization
        if not recommendations:
            recommendations.append("System performance appears optimal")
            recommendations.append("Consider implementing data prefetching for I/O bound operations")

        return recommendations

    def export_metrics(self, file_path: Union[str, Path]) -> bool:
        """
        Export performance metrics to JSON file.

        Args:
            file_path: Path to export metrics

        Returns:
            True if export successful, False otherwise
        """
        try:
            file_path = Path(file_path)
            file_path.parent.mkdir(parents=True, exist_ok=True)

            with self.history_lock:
                metrics_data = [m.to_dict() for m in self.metrics_history]

            export_data = {
                'export_timestamp': datetime.now().isoformat(),
                'total_samples': len(metrics_data),
                'monitoring_interval': self.monitoring_interval,
                'system_info': {
                    'cpu_count': psutil.cpu_count(),
                    'memory_total_gb': psutil.virtual_memory().total / (1024**3),
                    'gpu_available': GPU_AVAILABLE
                },
                'metrics': metrics_data
            }

            with open(file_path, 'w') as f:
                json.dump(export_data, f, indent=2)

            logger.info(f"Performance metrics exported to {file_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to export metrics: {e}")
            return False

    def create_performance_report(self) -> str:
        """
        Create a comprehensive performance report.

        Returns:
            Formatted performance report string
        """
        summary = self.get_performance_summary(300)
        bottlenecks = self.identify_bottlenecks()
        recommendations = self.generate_optimization_recommendations()

        report = []
        report.append("=" * 60)
        report.append("METHYLUTILS PERFORMANCE REPORT")
        report.append("=" * 60)
        report.append("")

        if summary:
            report.append("PERFORMANCE SUMMARY (Last 5 minutes)")
            report.append("-" * 40)
            report.append(".1f")
            report.append(".1f")
            report.append(".2f")
            report.append(".1f")
            report.append(".1f")
            if summary.get('gpu_utilization_avg_percent', 0) > 0:
                report.append(".1f")
            report.append("")

        report.append("POTENTIAL BOTTLENECKS")
        report.append("-" * 40)
        for bottleneck in bottlenecks:
            report.append(f"• {bottleneck}")
        report.append("")

        report.append("OPTIMIZATION RECOMMENDATIONS")
        report.append("-" * 40)
        for recommendation in recommendations:
            report.append(f"• {recommendation}")
        report.append("")

        report.append("=" * 60)

        return "\n".join(report)

# Decorator for automatic performance profiling
def profile_performance(operation_name: str = None):
    """
    Decorator to automatically profile function performance.

    Args:
        operation_name: Name of the operation (defaults to function name)
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            profiler = get_performance_profiler()
            op_name = operation_name or func.__name__

            profiler.start_operation(op_name)
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                profiler.end_operation()

        return wrapper
    return decorator

# Global profiler instance
_profiler = None

def get_performance_profiler() -> PerformanceProfiler:
    """Get global performance profiler instance."""
    global _profiler
    if _profiler is None:
        _profiler = PerformanceProfiler()
    return _profiler

def start_performance_monitoring():
    """Start global performance monitoring."""
    profiler = get_performance_profiler()
    profiler.start_monitoring()

def stop_performance_monitoring():
    """Stop global performance monitoring."""
    profiler = get_performance_profiler()
    profiler.stop_monitoring()

def get_performance_report() -> str:
    """Get current performance report."""
    profiler = get_performance_profiler()
    return profiler.create_performance_report()

if __name__ == "__main__":
    # Test the performance profiler
    profiler = get_performance_profiler()

    print("📊 MethylUtils Performance Profiler")
    print("=" * 40)

    # Start monitoring
    profiler.start_monitoring()
    time.sleep(2)  # Collect some data

    # Get current metrics
    metrics = profiler.get_current_metrics()
    print("Current Metrics:")
    print(".1f")
    print(".1f")
    print(".2f")

    # Generate report
    report = profiler.create_performance_report()
    print("\nPerformance Report:")
    print(report)

    # Stop monitoring
    profiler.stop_monitoring()

    print("\n✅ Performance profiler ready!")
    print("Use get_performance_profiler() for monitoring genome processing")
