#!/bin/bash
# Fix Docker permissions for ubuntu user

set -e

echo "Fixing Docker permissions for ubuntu user..."

# Check if we're running as ubuntu user
if [ "$USER" != "ubuntu" ]; then
    echo "Warning: This script should be run as the ubuntu user"
    echo "Current user: $USER"
fi

# Add ubuntu user to docker group (requires sudo)
echo "1. Adding ubuntu user to docker group..."
sudo usermod -aG docker ubuntu

# Set Docker socket permissions
echo "2. Setting Docker socket permissions..."
sudo chmod 666 /var/run/docker.sock

# Verify group membership
echo "3. Verifying group membership..."
groups ubuntu

echo ""
echo "✅ Docker permissions configured!"
echo ""
echo "⚠️  IMPORTANT: You need to restart your session for the group changes to take effect."
echo ""
echo "To restart your session:"
echo "1. Log out and log back in, OR"
echo "2. Run: newgrp docker, OR"
echo "3. Restart Cursor completely"
echo ""
echo "After restarting, test Docker access:"
echo "  docker ps"
echo ""
echo "If Docker still doesn't work, try the alternative solutions in the README."
