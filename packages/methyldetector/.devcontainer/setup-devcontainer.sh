#!/bin/bash
# Setup script for MethylDetector devcontainer

set -e

echo "Setting up MethylDetector development container..."

# Check if we're in the right directory
if [ ! -f "pyproject.toml" ]; then
    echo "Error: Please run this script from the MethylDetector root directory"
    exit 1
fi

# Check if Docker is running
if ! docker info >/dev/null 2>&1; then
    echo "Error: Docker is not running. Please start Docker first."
    exit 1
fi

# Check if the cuda directory exists
if [ ! -d "/home/ubuntu/Work/cuda" ]; then
    echo "Error: CUDA directory not found at /home/ubuntu/Work/cuda"
    echo "Please ensure the container setup is in the correct location."
    exit 1
fi

echo "1. Building goliath container..."
cd /home/ubuntu/Work/cuda
docker-compose build

echo "2. Starting goliath container..."
docker-compose up -d

echo "3. Waiting for container to be ready..."
sleep 5

echo "4. Checking container status..."
if docker ps | grep -q goliath; then
    echo "✅ goliath container is running"
else
    echo "❌ goliath container failed to start"
    exit 1
fi

echo "5. Setting up MethylUtils packages..."
docker exec goliath /home/ubuntu/Work/cuda/setup_methyl_packages.sh

echo "6. Verifying setup..."
docker exec goliath python -c "
import sys
print(f'Python path: {sys.executable}')
try:
    from methyl_utils.gpu_detection import print_gpu_status
    print_gpu_status()
except ImportError as e:
    print(f'Warning: MethylUtils not properly installed: {e}')
"

echo "7. Testing MethylDetector imports..."
docker exec goliath bash -c "cd /workspace && python -c 'import methyl_detector; print(\"MethylDetector imported successfully\")'"

echo ""
echo "🎉 Development container setup complete!"
echo ""
echo "Next steps:"
echo "1. Open Cursor"
echo "2. Open the project folder"
echo "3. Click 'Reopen in Container' when prompted"
echo ""
echo "The devcontainer will automatically:"
echo "- Mount all necessary volumes"
echo "- Set up the Python environment"
echo "- Install development dependencies"
echo "- Configure GPU support"
echo ""
echo "To manually enter the container:"
echo "  docker exec -it goliath bash"
echo ""
echo "To stop the container:"
echo "  cd /home/ubuntu/Work/cuda && docker-compose down"
