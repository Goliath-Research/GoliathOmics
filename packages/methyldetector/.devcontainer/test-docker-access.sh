#!/bin/bash
# Test Docker access for ubuntu user

echo "Testing Docker access for ubuntu user..."
echo "========================================"

# Test 1: Check if Docker is running
echo "1. Checking if Docker daemon is running..."
if systemctl is-active --quiet docker; then
    echo "   ✅ Docker daemon is running"
else
    echo "   ❌ Docker daemon is not running"
    echo "   Run: sudo systemctl start docker"
    exit 1
fi

# Test 2: Check Docker socket permissions
echo ""
echo "2. Checking Docker socket permissions..."
if [ -S /var/run/docker.sock ]; then
    PERMS=$(ls -la /var/run/docker.sock | awk '{print $1}')
    echo "   Docker socket permissions: $PERMS"
    if [[ $PERMS == *"rw"* ]]; then
        echo "   ✅ Docker socket is readable/writable"
    else
        echo "   ❌ Docker socket permissions insufficient"
        echo "   Run: sudo chmod 666 /var/run/docker.sock"
    fi
else
    echo "   ❌ Docker socket not found"
    exit 1
fi

# Test 3: Check if ubuntu user is in docker group
echo ""
echo "3. Checking if ubuntu user is in docker group..."
if groups ubuntu | grep -q docker; then
    echo "   ✅ ubuntu user is in docker group"
else
    echo "   ❌ ubuntu user is NOT in docker group"
    echo "   Run: sudo usermod -aG docker ubuntu"
    echo "   Then restart your session"
fi

# Test 4: Test Docker command without sudo
echo ""
echo "4. Testing Docker command without sudo..."
if docker ps >/dev/null 2>&1; then
    echo "   ✅ Docker command works without sudo"
    echo "   Available containers:"
    docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
else
    echo "   ❌ Docker command fails without sudo"
    echo "   Error: $(docker ps 2>&1 | head -1)"
fi

# Test 5: Check if goliath container exists
echo ""
echo "5. Checking for goliath container..."
if docker ps -a | grep -q goliath; then
    echo "   ✅ goliath container found"
    if docker ps | grep -q goliath; then
        echo "   ✅ goliath container is running"
    else
        echo "   ⚠️  goliath container exists but is not running"
        echo "   Run: cd /home/ubuntu/Work/cuda && docker-compose up -d"
    fi
else
    echo "   ❌ goliath container not found"
    echo "   Run: cd /home/ubuntu/Work/cuda && docker-compose build && docker-compose up -d"
fi

echo ""
echo "========================================"
echo "Summary:"

if docker ps >/dev/null 2>&1; then
    echo "✅ Docker access is working!"
    echo ""
    echo "Next steps:"
    echo "1. Open Cursor"
    echo "2. Open project folder"
    echo "3. Click 'Reopen in Container' when prompted"
else
    echo "❌ Docker access is not working"
    echo ""
    echo "Please try the solutions in DOCKER_PERMISSIONS_GUIDE.modeler"
    echo "Or run: ./.devcontainer/fix-docker-permissions.sh"
fi
