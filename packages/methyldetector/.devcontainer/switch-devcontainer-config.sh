#!/bin/bash
# Switch between devcontainer configurations

set -e

echo "MethylDetector DevContainer Configuration Switcher"
echo "================================================="

# Check current configuration
if [ -f "devcontainer.json" ] && [ -f "devcontainer-simple.json" ]; then
    echo "Both configurations are available."
    echo ""
    echo "Current configuration:"
    if grep -q "dockerComposeFile" devcontainer.json; then
        echo "  📋 Docker Compose method (devcontainer.json)"
    elif grep -q "image.*goliath" devcontainer.json; then
        echo "  🖼️  Simple image method (devcontainer.json)"
    else
        echo "  ❓ Unknown configuration"
    fi
    echo ""
    echo "Available configurations:"
    echo "1. Docker Compose method (recommended for most cases)"
    echo "2. Simple image method (if Docker Compose fails)"
    echo ""
    read -p "Choose configuration (1 or 2): " choice
    
    case $choice in
        1)
            echo "Switching to Docker Compose method..."
            mv devcontainer.json devcontainer-simple.json.bak 2>/dev/null || true
            mv devcontainer-compose.json devcontainer.json 2>/dev/null || true
            echo "✅ Switched to Docker Compose method"
            ;;
        2)
            echo "Switching to simple image method..."
            mv devcontainer.json devcontainer-compose.json 2>/dev/null || true
            mv devcontainer-simple.json devcontainer.json 2>/dev/null || true
            echo "✅ Switched to simple image method"
            ;;
        *)
            echo "Invalid choice. No changes made."
            exit 1
            ;;
    esac
else
    echo "Setting up configurations..."
    
    # Create backup of current config
    if [ -f "devcontainer.json" ]; then
        cp devcontainer.json devcontainer-compose.json
        echo "✅ Created devcontainer-compose.json"
    fi
    
    # Ensure simple config exists
    if [ ! -f "devcontainer-simple.json" ]; then
        echo "❌ devcontainer-simple.json not found"
        exit 1
    fi
    
    echo "✅ Configurations ready"
    echo "Run this script again to switch between methods"
fi

echo ""
echo "Next steps:"
echo "1. Open Cursor"
echo "2. Open project folder"
echo "3. Click 'Reopen in Container'"
echo ""
echo "If it fails, run this script again and try the other method."
