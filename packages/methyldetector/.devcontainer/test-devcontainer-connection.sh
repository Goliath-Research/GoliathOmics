#!/bin/bash
# Test devcontainer connection methods

set -e

echo "Testing MethylDetector devcontainer connection methods..."
echo "========================================================"

# Check if goliath container is running
echo "1. Checking if goliath container is running..."
if docker ps | grep -q goliath; then
    echo "   ✅ goliath container is running"
    CONTAINER_STATUS="running"
else
    echo "   ❌ goliath container is not running"
    echo "   Starting container..."
    cd /home/ubuntu/Work/cuda
    docker-compose up -d
    sleep 5
    if docker ps | grep -q goliath; then
        echo "   ✅ goliath container started successfully"
        CONTAINER_STATUS="started"
    else
        echo "   ❌ Failed to start goliath container"
        exit 1
    fi
fi

# Test Docker Compose configuration
echo ""
echo "2. Testing Docker Compose configuration..."
if docker compose -f /home/ubuntu/Work/cuda/docker-compose.yml config >/dev/null 2>&1; then
    echo "   ✅ Docker Compose configuration is valid"
else
    echo "   ❌ Docker Compose configuration has issues"
    echo "   This might cause problems with the devcontainer"
fi

# Test container access
echo ""
echo "3. Testing container access..."
if docker exec goliath echo "Container accessible" >/dev/null 2>&1; then
    echo "   ✅ Can access goliath container"
else
    echo "   ❌ Cannot access goliath container"
    exit 1
fi

# Test Python environment in container
echo ""
echo "4. Testing Python environment in container..."
if docker exec goliath python -c "import sys; print(f'Python: {sys.executable}')" >/dev/null 2>&1; then
    echo "   ✅ Python environment is working"
    PYTHON_PATH=$(docker exec goliath python -c "import sys; print(sys.executable)")
    echo "   Python path: $PYTHON_PATH"
else
    echo "   ❌ Python environment has issues"
fi

# Test MethylUtils access
echo ""
echo "5. Testing MethylUtils access..."
if docker exec goliath python -c "from methyl_utils.gpu_detection import print_gpu_status" >/dev/null 2>&1; then
    echo "   ✅ MethylUtils is accessible"
else
    echo "   ⚠️  MethylUtils not accessible (may need setup)"
    echo "   Run: docker exec goliath /home/ubuntu/Work/cuda/setup_methyl_packages.sh"
fi

echo ""
echo "========================================================"
echo "Connection test complete!"
echo ""

# Provide recommendations
if [ "$CONTAINER_STATUS" = "running" ] || [ "$CONTAINER_STATUS" = "started" ]; then
    echo "✅ Container is ready for devcontainer connection"
    echo ""
    echo "Next steps:"
    echo "1. Open Cursor"
    echo "2. Open project folder"
    echo "3. Try 'Reopen in Container'"
    echo ""
    echo "If the Docker Compose method fails, try the simple method:"
    echo "1. Rename devcontainer.json to devcontainer-compose.json"
    echo "2. Rename devcontainer-simple.json to devcontainer.json"
    echo "3. Try 'Reopen in Container' again"
else
    echo "❌ Container setup failed"
    echo "Please check the Docker setup and try again"
fi
