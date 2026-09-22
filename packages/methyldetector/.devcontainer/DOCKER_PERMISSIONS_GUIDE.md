# Docker Permissions Guide for Cursor DevContainer

## Problem
Cursor runs as the `ubuntu` user and cannot access Docker without sudo privileges, preventing the devcontainer from connecting to the goliath container.

## Solutions

### Solution 1: Add User to Docker Group (Recommended)

This is the standard and recommended approach:

```bash
# Run the provided script
./.devcontainer/fix-docker-permissions.sh

# OR manually:
sudo usermod -aG docker ubuntu
sudo chmod 666 /var/run/docker.sock
```

**Important**: After running this, you MUST restart your session:
- Log out and log back in, OR
- Run `newgrp docker`, OR
- Restart Cursor completely

### Solution 2: Use Docker with Sudo (Temporary Fix)

If you can't restart your session immediately, you can configure Cursor to use sudo with Docker:

1. **Create a Docker wrapper script**:
```bash
# Create the wrapper
cat > ~/.local/bin/docker-sudo << 'EOF'
#!/bin/bash
sudo docker "$@"
EOF

chmod +x ~/.local/bin/docker-sudo
```

2. **Update your PATH** to use the wrapper:
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

3. **Test the wrapper**:
```bash
docker-sudo ps
```

### Solution 3: Configure Cursor to Use Sudo

If the above solutions don't work, you can configure Cursor to use sudo with Docker by modifying the devcontainer configuration:

1. **Create a custom Docker Compose override**:
```yaml
# .devcontainer/docker-compose.override.yml
version: '3.8'
services:
  gpu_env:
    command: sleep infinity
    # Add any additional overrides here
```

2. **Update devcontainer.json** to use sudo:
```json
{
  "initializeCommand": "bash -c 'if ! sudo docker ps >/dev/null 2>&1; then echo \"Docker not accessible with sudo\"; exit 1; fi'",
  "dockerComposeFile": [
    "../Work/cuda/docker-compose.yml",
    "docker-compose.override.yml"
  ]
}
```

### Solution 4: Manual Container Management

If Cursor still can't connect, you can manually manage the container and use Cursor's remote development features:

1. **Start the container manually**:
```bash
cd /home/ubuntu/Work/cuda
sudo docker-compose up -d
```

2. **Connect to the running container**:
   - In Cursor, use "Remote-Containers: Attach to Running Container"
   - Select the `goliath` container
   - Choose the project folder as the workspace folder

### Solution 5: Use VS Code Server in Container

If all else fails, you can run VS Code Server directly in the container:

1. **Start the container**:
```bash
cd /home/ubuntu/Work/cuda
sudo docker-compose up -d
```

2. **Install VS Code Server in the container**:
```bash
sudo docker exec -it goliath bash
# Inside the container:
curl -fsSL https://code-server.dev/install.sh | sh
code-server --bind-addr 0.0.0.0:8080 --auth none
```

3. **Access via web browser**:
   - Open `http://localhost:8080` in your browser
   - Open the project folder

## Verification Steps

After applying any solution, verify it works:

```bash
# Test Docker access
docker ps

# Test container is running
docker ps | grep goliath

# Test container access
docker exec goliath echo "Container accessible"
```

## Troubleshooting

### "Permission denied" errors
- Ensure the ubuntu user is in the docker group: `groups ubuntu`
- Restart your session after adding to docker group
- Check Docker socket permissions: `ls -la /var/run/docker.sock`

### "Container not found" errors
- Ensure the goliath container is running: `docker ps | grep goliath`
- Start the container: `cd /home/ubuntu/Work/cuda && docker-compose up -d`

### Cursor still can't connect
- Try restarting Cursor completely
- Check Cursor's Docker extension settings
- Use the manual container attachment method (Solution 4)

## Recommended Workflow

1. **First, try Solution 1** (add to docker group) - this is the cleanest approach
2. **If you can't restart immediately**, use Solution 2 (sudo wrapper) as a temporary fix
3. **If Cursor still has issues**, use Solution 4 (manual container management)
4. **As a last resort**, use Solution 5 (VS Code Server in container)

## Additional Notes

- The devcontainer configuration includes a pre-check that will warn you if Docker isn't accessible
- All solutions maintain the same development environment and functionality
- The goliath container provides the same GPU support and package access regardless of connection method
