#!/usr/bin/env python3
"""
MethylUtils Monitoring and Health Check Module
==============================================

Provides comprehensive monitoring capabilities for genome-scale methylation analysis,
including Prometheus metrics, health checks, and performance profiling.

Features:
- Prometheus metrics export
- Health check endpoints
- Real-time performance monitoring
- GPU memory and utilization tracking
- System resource monitoring
- Error tracking and alerting
"""

import time
import threading
import logging
from typing import Dict, List, Any, Optional
from pathlib import Path
import psutil
import GPUtil

# Optional imports for production monitoring
try:
    from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry, generate_latest
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    # Fallback implementations
    class Counter:
        def __init__(self, *args, **kwargs): pass
        def inc(self, value=1): pass
        def labels(self, **kwargs): return self

    class Gauge:
        def __init__(self, *args, **kwargs): pass
        def set(self, value): pass
        def labels(self, **kwargs): return self

    class Histogram:
        def __init__(self, *args, **kwargs): pass
        def observe(self, value): pass
        def labels(self, **kwargs): return self

    CollectorRegistry = object
    generate_latest = lambda x: b""

# Import MethylUtils components
try:
    from .gpu_detection import is_gpu_available, get_gpu_info
    from .memory_manager import get_memory_manager
    from .performance_profiler import get_performance_profiler
except ImportError:
    # Fallback for when modules aren't available
    is_gpu_available = lambda: False
    get_gpu_info = lambda: {}
    get_memory_manager = lambda: None
    get_performance_profiler = lambda: None

logger = logging.getLogger(__name__)

class MethylUtilsMonitor:
    """
    Comprehensive monitoring system for MethylUtils.

    Provides real-time monitoring of:
    - GPU utilization and memory
    - System resources (CPU, memory, disk)
    - Application performance metrics
    - Error rates and health status
    - Custom business metrics
    """

    def __init__(self, registry: Optional[CollectorRegistry] = None):
        """
        Initialize the monitoring system.

        Args:
            registry: Prometheus registry (auto-created if None)
        """
        self.registry = registry or CollectorRegistry()

        # GPU Metrics
        self.gpu_memory_used = Gauge(
            'methylutils_gpu_memory_used_bytes',
            'GPU memory currently used in bytes',
            registry=self.registry
        )

        self.gpu_memory_total = Gauge(
            'methylutils_gpu_memory_total_bytes',
            'Total GPU memory in bytes',
            registry=self.registry
        )

        self.gpu_utilization = Gauge(
            'methylutils_gpu_utilization_percent',
            'GPU utilization percentage',
            registry=self.registry
        )

        # System Metrics
        self.cpu_usage = Gauge(
            'methylutils_cpu_usage_percent',
            'CPU usage percentage',
            registry=self.registry
        )

        self.memory_used = Gauge(
            'methylutils_memory_used_bytes',
            'System memory used in bytes',
            registry=self.registry
        )

        self.memory_total = Gauge(
            'methylutils_memory_total_bytes',
            'Total system memory in bytes',
            registry=self.registry
        )

        # Application Metrics
        self.active_operations = Gauge(
            'methylutils_active_operations',
            'Number of currently active operations',
            registry=self.registry
        )

        self.operations_total = Counter(
            'methylutils_operations_total',
            'Total number of operations completed',
            ['operation_type', 'status'],
            registry=self.registry
        )

        self.operation_duration = Histogram(
            'methylutils_operation_duration_seconds',
            'Operation duration in seconds',
            ['operation_type'],
            registry=self.registry
        )

        # Genome Processing Metrics
        self.positions_processed = Counter(
            'methylutils_positions_processed_total',
            'Total number of genomic positions processed',
            registry=self.registry
        )

        self.chunk_processing_time = Histogram(
            'methylutils_chunk_processing_time_seconds',
            'Time to process a single chunk',
            registry=self.registry
        )

        self.memory_cleanup_operations = Counter(
            'methylutils_memory_cleanup_operations_total',
            'Total number of memory cleanup operations',
            registry=self.registry
        )

        # Error Metrics
        self.errors_total = Counter(
            'methylutils_errors_total',
            'Total number of errors',
            ['error_type'],
            registry=self.registry
        )

        # Health Status
        self.health_status = Gauge(
            'methylutils_health_status',
            'Overall health status (1=healthy, 0=unhealthy)',
            registry=self.registry
        )

        # Monitoring state
        self.monitoring_active = False
        self.monitor_thread = None
        self.last_update = 0
        self.update_interval = 5.0  # seconds

        # Initialize components
        self.memory_manager = get_memory_manager()
        self.performance_profiler = get_performance_profiler()

        logger.info("MethylUtils monitoring system initialized")

    def start_monitoring(self):
        """Start the monitoring system."""
        if self.monitoring_active:
            logger.warning("Monitoring already active")
            return

        self.monitoring_active = True
        self.monitor_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.monitor_thread.start()

        logger.info("MethylUtils monitoring started")

    def stop_monitoring(self):
        """Stop the monitoring system."""
        if not self.monitoring_active:
            return

        self.monitoring_active = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5.0)

        logger.info("MethylUtils monitoring stopped")

    def _monitoring_loop(self):
        """Main monitoring loop."""
        while self.monitoring_active:
            try:
                self._update_metrics()
                time.sleep(self.update_interval)
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                self.errors_total.labels(error_type='monitoring_loop').inc()
                time.sleep(self.update_interval)

    def _update_metrics(self):
        """Update all monitoring metrics."""
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return

        self.last_update = current_time

        # Update GPU metrics
        self._update_gpu_metrics()

        # Update system metrics
        self._update_system_metrics()

        # Update application metrics
        self._update_application_metrics()

        # Update health status
        self._update_health_status()

    def _update_gpu_metrics(self):
        """Update GPU-related metrics."""
        try:
            if is_gpu_available():
                gpu_info = get_gpu_info()
                if gpu_info:
                    # GPU memory metrics
                    gpu_memory_used_mb = gpu_info.get('memory_used', 0)
                    gpu_memory_total_mb = gpu_info.get('memory_total', 0)

                    self.gpu_memory_used.set(gpu_memory_used_mb * 1024 * 1024)
                    self.gpu_memory_total.set(gpu_memory_total_mb * 1024 * 1024)

                    # GPU utilization
                    utilization = gpu_info.get('utilization', 0)
                    self.gpu_utilization.set(utilization)

            # GPUtil fallback
            try:
                gpus = GPUtil.getGPUs()
                if gpus:
                    gpu = gpus[0]
                    self.gpu_memory_used.set(gpu.memoryUsed * 1024 * 1024)
                    self.gpu_memory_total.set(gpu.memoryTotal * 1024 * 1024)
                    self.gpu_utilization.set(gpu.load * 100)
            except:
                pass

        except Exception as e:
            logger.debug(f"Error updating GPU metrics: {e}")

    def _update_system_metrics(self):
        """Update system resource metrics."""
        try:
            # CPU usage
            self.cpu_usage.set(psutil.cpu_percent(interval=None))

            # Memory usage
            memory = psutil.virtual_memory()
            self.memory_used.set(memory.used)
            self.memory_total.set(memory.total)

        except Exception as e:
            logger.debug(f"Error updating system metrics: {e}")

    def _update_application_metrics(self):
        """Update application-specific metrics."""
        try:
            # Memory manager metrics
            if self.memory_manager:
                usage = self.memory_manager.get_memory_usage()
                # Additional memory metrics could be added here

            # Performance profiler metrics
            if self.performance_profiler:
                # Could integrate performance metrics here
                pass

        except Exception as e:
            logger.debug(f"Error updating application metrics: {e}")

    def _update_health_status(self):
        """Update overall health status."""
        try:
            health_score = 1.0

            # Check GPU availability
            if not is_gpu_available():
                health_score *= 0.8

            # Check system memory
            memory = psutil.virtual_memory()
            if memory.percent > 90:
                health_score *= 0.7

            # Check recent errors (simplified)
            # In a real implementation, you'd track error rates over time

            self.health_status.set(health_score)

        except Exception as e:
            logger.debug(f"Error updating health status: {e}")
            self.health_status.set(0.5)  # Degraded but not critical

    def record_operation(self, operation_type: str, duration: float, status: str = 'success'):
        """
        Record an operation for monitoring.

        Args:
            operation_type: Type of operation (e.g., 'distance_calculation')
            duration: Operation duration in seconds
            status: Operation status ('success', 'error', etc.)
        """
        self.operations_total.labels(operation_type=operation_type, status=status).inc()
        self.operation_duration.labels(operation_type=operation_type).observe(duration)

        if status == 'success':
            self.active_operations.dec()
        else:
            self.errors_total.labels(error_type=f'{operation_type}_error').inc()

    def record_positions_processed(self, count: int):
        """
        Record the number of genomic positions processed.

        Args:
            count: Number of positions processed
        """
        self.positions_processed.inc(count)

    def record_chunk_processing(self, duration: float):
        """
        Record chunk processing time.

        Args:
            duration: Time to process chunk in seconds
        """
        self.chunk_processing_time.observe(duration)

    def record_memory_cleanup(self):
        """Record a memory cleanup operation."""
        self.memory_cleanup_operations.inc()

    def get_metrics_text(self) -> str:
        """
        Get metrics in Prometheus text format.

        Returns:
            Metrics as a text string
        """
        if PROMETHEUS_AVAILABLE:
            return generate_latest(self.registry).decode('utf-8')
        else:
            return "# Prometheus not available\n"

    def get_health_status(self) -> Dict[str, Any]:
        """
        Get comprehensive health status information.

        Returns:
            Dictionary with health status details
        """
        # Get health score from Prometheus Gauge
        try:
            health_score = float(self.health_status.collect()[0].samples[0].value)
        except (AttributeError, IndexError):
            health_score = 1.0
        health_info = {
            'timestamp': time.time(),
            'status': 'healthy' if health_score > 0.8 else 'degraded',
            'health_score': health_score,
            'gpu_available': is_gpu_available(),
            'system_memory_percent': psutil.virtual_memory().percent,
            'cpu_percent': psutil.cpu_percent(),
            'active_operations': self.active_operations._value,
        }

        # Add GPU info if available
        if is_gpu_available():
            gpu_info = get_gpu_info()
            if gpu_info:
                health_info.update({
                    'gpu_memory_used_mb': gpu_info.get('memory_used', 0),
                    'gpu_memory_total_mb': gpu_info.get('memory_total', 0),
                    'gpu_utilization_percent': gpu_info.get('utilization', 0),
                })

        return health_info

    def start_operation(self, operation_type: str):
        """
        Mark the start of an operation.

        Args:
            operation_type: Type of operation starting
        """
        self.active_operations.inc()

    def end_operation(self, operation_type: str, duration: float, status: str = 'success'):
        """
        Mark the end of an operation.

        Args:
            operation_type: Type of operation ending
            duration: Operation duration in seconds
            status: Operation status
        """
        self.record_operation(operation_type, duration, status)


# Global monitor instance
_monitor_instance = None
_monitor_lock = threading.Lock()

def get_monitor() -> MethylUtilsMonitor:
    """
    Get the global MethylUtils monitor instance.

    Returns:
        MethylUtilsMonitor instance
    """
    global _monitor_instance

    if _monitor_instance is None:
        with _monitor_lock:
            if _monitor_instance is None:
                _monitor_instance = MethylUtilsMonitor()

    return _monitor_instance

def start_monitoring():
    """Start the global monitoring system."""
    monitor = get_monitor()
    monitor.start_monitoring()

def stop_monitoring():
    """Stop the global monitoring system."""
    monitor = get_monitor()
    monitor.stop_monitoring()

def record_operation(operation_type: str, duration: float, status: str = 'success'):
    """
    Record an operation in the global monitor.

    Args:
        operation_type: Type of operation
        duration: Operation duration in seconds
        status: Operation status
    """
    monitor = get_monitor()
    monitor.record_operation(operation_type, duration, status)

def get_health_status() -> Dict[str, Any]:
    """
    Get current health status from the global monitor.

    Returns:
        Health status dictionary
    """
    monitor = get_monitor()
    return monitor.get_health_status()


# Flask/HTTP endpoint for health checks and metrics
class HealthCheckServer:
    """
    Simple HTTP server for health checks and metrics endpoints.
    Can be used with gunicorn, uWSGI, or standalone.
    """

    def __init__(self, host: str = '0.0.0.0', port: int = 9090):
        self.host = host
        self.port = port
        self.monitor = get_monitor()

        # Try to import Flask
        try:
            from flask import Flask, jsonify, Response
            self.flask_available = True
            self.app = Flask(__name__)

            @self.app.route('/health')
            def health():
                health_data = self.monitor.get_health_status()
                status_code = 200 if health_data['status'] == 'healthy' else 503
                return jsonify(health_data), status_code

            @self.app.route('/metrics')
            def metrics():
                metrics_text = self.monitor.get_metrics_text()
                return Response(metrics_text, mimetype='text/plain')

            @self.app.route('/')
            def root():
                return jsonify({
                    'service': 'MethylUtils',
                    'version': __import__('methyl_utils').__version__,
                    'status': 'running',
                    'endpoints': {
                        '/health': 'Health check endpoint',
                        '/metrics': 'Prometheus metrics endpoint'
                    }
                })

        except ImportError:
            self.flask_available = False
            logger.warning("Flask not available - health check server disabled")

    def start(self):
        """Start the health check server."""
        if not self.flask_available:
            logger.error("Cannot start health check server - Flask not available")
            return

        logger.info(f"Starting health check server on {self.host}:{self.port}")

        # Start in a separate thread for non-blocking operation
        server_thread = threading.Thread(
            target=lambda: self.app.run(host=self.host, port=self.port, debug=False),
            daemon=True
        )
        server_thread.start()

    def stop(self):
        """Stop the health check server."""
        # Flask doesn't have a built-in stop method for threaded servers
        # In production, use a proper WSGI server with signal handling
        pass


# Initialize monitoring on import
def init_monitoring():
    """Initialize the monitoring system."""
    monitor = get_monitor()
    monitor.start_monitoring()

    # Start health check server if in production
    import os
    if os.getenv('METHYLUTILS_PROFILE', '').lower() == 'true':
        health_server = HealthCheckServer()
        health_server.start()

# Auto-initialize if running as main module
if __name__ == '__main__':
    init_monitoring()

    # Example usage
    monitor = get_monitor()

    # Simulate some operations
    import time
    for i in range(10):
        monitor.start_operation('test_operation')
        time.sleep(0.1)
        monitor.end_operation('test_operation', 0.1, 'success')
        monitor.record_positions_processed(1000000)

    print("Monitoring test completed")
    print("Health status:", monitor.get_health_status())
