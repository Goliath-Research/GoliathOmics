#!/usr/bin/env python3
"""
Entry point for methyl_detector package.
This allows running the package with: python -m methyl_detector
"""

import sys
import signal

# Import cleanup functions
try:
    from .utils.core import cleanup_gpu_memory, register_global_cleanup_handlers
    
    # Set up global signal handlers for GPU cleanup
    def global_signal_handler(signum, frame):
        signal_name = signal.Signals(signum).name if hasattr(signal.Signals, signum) else str(signum)
        print(f"\n🚨 Global signal handler: Received {signal_name} ({signum}), cleaning up GPU memory...")
        cleanup_gpu_memory()
        print("🔄 Exiting gracefully...")
        sys.exit(1)
    
    def global_exception_handler(exc_type, exc_value, exc_traceback):
        """Global exception handler for GPU cleanup."""
        print(f"\n💥 Global exception handler: {exc_type.__name__}: {exc_value}")
        cleanup_gpu_memory()
        print("🔄 Exiting due to exception...")
    
    # Register global signal handlers
    signal.signal(signal.SIGINT, global_signal_handler)    # Ctrl+C
    signal.signal(signal.SIGTERM, global_signal_handler)   # Termination signal
    signal.signal(signal.SIGHUP, global_signal_handler)    # Hangup signal
    signal.signal(signal.SIGQUIT, global_signal_handler)   # Quit signal
    signal.signal(signal.SIGABRT, global_signal_handler)   # Abort signal
    
    # Set global exception handler
    sys.excepthook = global_exception_handler
    
    # Register cleanup for normal exit and mark as globally registered
    register_global_cleanup_handlers()
    
    print("🛡️  Global GPU cleanup handlers registered")
    
except ImportError:
    # If imports fail, continue without cleanup (for non-GPU systems)
    pass

from .cli.main import main

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🚨 Keyboard interrupt detected, cleaning up...")
        try:
            cleanup_gpu_memory()
        except Exception as e:
            print(f"Error cleaning up GPU memory: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Fatal error: {e}")
        try:
            cleanup_gpu_memory()
        except Exception as e:
            print(f"Error cleaning up GPU memory: {e}")
        sys.exit(1) 