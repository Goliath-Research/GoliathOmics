#!/usr/bin/env python3
"""
GPU Task Runner with Memory Management

This script manages GPU memory by restarting the container between heavy tasks
to prevent memory fragmentation and hanging memory.
"""

import subprocess
import json
import time
import logging
import argparse
from pathlib import Path
from typing import List, Dict, Any
import docker
from docker.errors import DockerException

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class GPUTaskRunner:
    def __init__(self, container_name: str = "goliath", 
                 working_dir: str = "/home/ubuntu/MethylCentroid",
                 compose_file: str = "/home/ubuntu/Work/cuda/docker-compose.yml"):
        self.container_name = container_name
        self.working_dir = working_dir
        self.compose_file = compose_file
        self.client = docker.from_env()
        
    def restart_container(self) -> bool:
        """Restart the container to clear GPU memory."""
        try:
            logger.info(f"Restarting container {self.container_name}...")
            
            # Stop container
            subprocess.run([
                "docker", "compose", "-f", self.compose_file, "down"
            ], check=True, capture_output=True)
            
            # Start container
            subprocess.run([
                "docker", "compose", "-f", self.compose_file, "up", "-d"
            ], check=True, capture_output=True)
            
            # Wait for container to be ready
            time.sleep(10)
            
            # Verify container is running
            container = self.client.containers.get(self.container_name)
            if container.status == "running":
                logger.info("Container restarted successfully")
                return True
            else:
                logger.error(f"Container failed to start: {container.status}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to restart container: {e}")
            return False
    
    def run_task(self, command: List[str], restart_after: bool = True) -> Dict[str, Any]:
        """Run a single task in the container."""
        try:
            logger.info(f"Running task: {' '.join(command)}")
            
            # Execute command in container
            result = subprocess.run([
                "docker", "exec", "-w", self.working_dir, 
                self.container_name
            ] + command, capture_output=True, text=True, timeout=3600)
            
            task_result = {
                "command": command,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "success": result.returncode == 0
            }
            
            if task_result["success"]:
                logger.info("Task completed successfully")
            else:
                logger.error(f"Task failed: {result.stderr}")
            
            # Restart container if requested
            if restart_after:
                self.restart_container()
            
            return task_result
            
        except subprocess.TimeoutExpired:
            logger.error("Task timed out after 1 hour")
            return {
                "command": command,
                "returncode": -1,
                "stdout": "",
                "stderr": "Task timed out",
                "success": False
            }
        except Exception as e:
            logger.error(f"Failed to run task: {e}")
            return {
                "command": command,
                "returncode": -1,
                "stdout": "",
                "stderr": str(e),
                "success": False
            }
    
    def run_task_queue(self, tasks: List[Dict[str, Any]], 
                      restart_between_tasks: bool = True) -> List[Dict[str, Any]]:
        """Run a queue of tasks with optional container restarts."""
        results = []
        
        for i, task in enumerate(tasks):
            logger.info(f"Processing task {i+1}/{len(tasks)}")
            
            # Check if task requires GPU
            requires_gpu = task.get("requires_gpu", True)
            
            if requires_gpu and restart_between_tasks and i > 0:
                logger.info("Restarting container for GPU memory cleanup...")
                if not self.restart_container():
                    logger.error("Failed to restart container, skipping task")
                    results.append({
                        "task": task,
                        "success": False,
                        "error": "Container restart failed"
                    })
                    continue
            
            # Run the task
            result = self.run_task(
                task["command"], 
                restart_after=(requires_gpu and restart_between_tasks)
            )
            
            results.append({
                "task": task,
                "result": result
            })
            
            # Add delay between tasks if specified
            if task.get("delay_seconds", 0) > 0:
                time.sleep(task["delay_seconds"])
        
        return results

def main():
    parser = argparse.ArgumentParser(description="GPU Task Runner with Memory Management")
    parser.add_argument("--config", required=True, help="JSON config file with task definitions")
    parser.add_argument("--container", default="goliath", help="Container name")
    parser.add_argument("--no-restart", action="store_true", help="Disable container restarts")
    
    args = parser.parse_args()
    
    # Load task configuration
    with open(args.config, 'r') as f:
        config = json.load(f)
    
    # Initialize task runner
    runner = GPUTaskRunner(container_name=args.container)
    
    # Run tasks
    results = runner.run_task_queue(
        config["tasks"], 
        restart_between_tasks=not args.no_restart
    )
    
    # Save results
    output_file = f"task_results_{int(time.time())}.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    logger.info(f"Task execution completed. Results saved to {output_file}")
    
    # Print summary
    successful = sum(1 for r in results if r.get("result", {}).get("success", False))
    total = len(results)
    logger.info(f"Successfully completed {successful}/{total} tasks")

if __name__ == "__main__":
    main()
