# MethylDetector Development Container

This directory contains the configuration for using Cursor (or VS Code) with the MethylDetector project inside the goliath container.

## Overview

The devcontainer configuration connects Cursor to the existing `goliath` container, which provides:
- NVIDIA CUDA 12.8 support with GPU acceleration
- Pre-installed scientific computing libraries (NumPy, SciPy, Pandas, CuPy, etc.)
- MethylUtils shared packages
- All necessary volume mounts for development

## Prerequisites

1. **Docker and Docker Compose** must be installed
2. **NVIDIA Container Toolkit** must be installed for GPU support
3. **The goliath container** must be built and running
4. **Docker permissions** must be configured for the ubuntu user (see troubleshooting below)

## Setup Instructions

### 1. Build and Start the goliath Container

```bash
# Navigate to the cuda directory
cd /home/ubuntu/Work/cuda

# Build the container
docker-compose build

# Start the container
docker-compose up -d

# Verify the container is running
docker ps | grep goliath
```

### 2. Install MethylUtils Packages (First Time Only)

```bash
# Enter the container
docker exec -it goliath bash

# Run the setup script
/home/ubuntu/Work/cuda/setup_methyl_packages.sh

# Exit the container
exit
```

### 3. Open in Cursor

1. Open Cursor
2. Open the project folder (e.g. MethylPipeline or MethylDetector)
3. Cursor should automatically detect the `.devcontainer/devcontainer.json` file
4. Click "Reopen in Container" when prompted

## What the Devcontainer Provides

### Volume Mounts
- Project root - The main project directory
- `/home/ubuntu/MethylUtils` - Shared utilities and packages
- `/home/ubuntu/Work` - Work directory for data and results
- `/home/ubuntu/working_dir` - Additional working directory

### Environment Variables
- `PYTHONPATH` - Includes MethylUtils in the Python path
- `METHYL_UTILS_PATH` - Points to MethylUtils directory
- `NVIDIA_VISIBLE_DEVICES=all` - Enables GPU access
- `CUDA_VISIBLE_DEVICES=0` - Sets primary GPU
- Various optimization settings for CUDA and threading

### Pre-installed Extensions
- Python support with IntelliSense
- Black formatter and isort
- Pylint, Flake8, and MyPy for code quality
- Jupyter notebook support
- JSON and YAML support
- Markdown support
- GitHub integration

### Development Features
- Automatic Poetry package installation on container creation
- GPU support for CuPy and CUDA operations
- Pre-configured Python interpreter (`/opt/methylutils/bin/python`)
- Format on save enabled
- Auto-organize imports on save

## Usage

### Running MethylDetector

Once inside the container, you can run MethylDetector as usual:

```bash
# Install dependencies (done automatically, but can be run manually)
poetry install --with dev

# Run tests
make test

# Run an example analysis
python -m methyl_detector examples/config_WT-msh1.json

# Run with verbose output
python -m methyl_detector examples/config_WT-msh1.json --verbose
```

### Using MethylUtils

The MethylUtils packages are automatically available:

```python
from methyl_utils.gpu_detection import print_gpu_status, is_gpu_available
from methyl_utils.logging_utils import setup_logging
from genomic_position_aligner.position_aligner import PositionAligner

# Check GPU status
print_gpu_status()

# Use GPU if available
if is_gpu_available():
    print("GPU is available for computation")
```

### Jupyter Notebooks

Jupyter notebooks are supported and can be accessed on:
- Port 8888 for Jupyter Notebook
- Port 8889 for Jupyter Lab

## Troubleshooting

### DevContainer Connection Issues (Most Common)

If Cursor fails to connect to the devcontainer:

```bash
# Test the complete setup
./.devcontainer/test-devcontainer-connection.sh

# If Docker Compose method fails, try the simple method
./.devcontainer/switch-devcontainer-config.sh

# Test Docker access if needed
./.devcontainer/test-docker-access.sh
```

**Common error**: "JSON parse error" or "Docker Compose configuration" errors
- **Solution**: Use the simple image method instead of Docker Compose
- **Quick fix**: Run `./.devcontainer/switch-devcontainer-config.sh` and choose option 2

**See `DOCKER_PERMISSIONS_GUIDE.modeler` for detailed solutions and alternatives.**

### Container Not Starting
```bash
# Check if the goliath container is running
docker ps | grep goliath

# If not running, start it
cd /home/ubuntu/Work/cuda
docker-compose up -d
```

### GPU Not Available
```bash
# Check GPU status inside the container
python -c "from methyl_utils.gpu_detection import print_gpu_status; print_gpu_status()"

# Check NVIDIA runtime
nvidia-smi
```

### Package Import Errors
```bash
# Reinstall MethylUtils packages
docker exec -it goliath /home/ubuntu/Work/cuda/setup_methyl_packages.sh
```

### Permission Issues
```bash
# Fix permissions
sudo chown -R ubuntu:ubuntu /home/ubuntu/MethylUtils
sudo chown -R ubuntu:ubuntu /path/to/project
```

## Development Workflow

1. **Code Changes**: Edit files in Cursor - changes are immediately reflected in the container
2. **Testing**: Run tests using `make test` or `poetry run pytest`
3. **Formatting**: Code is automatically formatted on save
4. **Linting**: Use the integrated linters for code quality
5. **GPU Development**: Use CuPy and CUDA features for high-performance computing

## Benefits

- **Consistent Environment**: Same setup across all developers
- **GPU Support**: Full CUDA and CuPy support for high-performance computing
- **Shared Packages**: Access to MethylUtils and other shared utilities
- **Integrated Development**: Full IDE features with container benefits
- **Easy Setup**: One-click container connection with Cursor

## File Structure

```
.devcontainer/
├── devcontainer.json    # Main devcontainer configuration
└── README.modeler           # This documentation
```

The devcontainer configuration references the existing Docker Compose setup in `/home/ubuntu/Work/cuda/docker-compose.yml` and uses the `goliath` container service.
