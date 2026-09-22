# DevContainer Troubleshooting Guide

## Common Issues and Solutions

### 1. JSON Parse Error / Docker Compose Configuration Error

**Error**: 
```
JSON parse error: at process.processTicksAndRejections
Command failed: docker compose -f /path/to/project/Work/cuda/docker-compose.yml --profile * config
```

**Cause**: The Docker Compose method is having issues with the `--profile *` parameter or path resolution.

**Solution**:
```bash
# Quick fix - switch to simple image method
./.devcontainer/switch-devcontainer-config.sh
# Choose option 2 (Simple image method)
```

**Alternative**: If you want to stick with Docker Compose method:
```bash
# Check if the path is correct
ls -la /home/ubuntu/Work/cuda/docker-compose.yml

# Test Docker Compose config
docker compose -f /home/ubuntu/Work/cuda/docker-compose.yml config
```

### 2. Container Not Found Error

**Error**: "Container not found" or "goliath container not running"

**Solution**:
```bash
# Start the container
cd /home/ubuntu/Work/cuda
docker-compose up -d

# Verify it's running
docker ps | grep goliath
```

### 3. Permission Denied Errors

**Error**: "Permission denied" when accessing Docker

**Solution**:
```bash
# Fix Docker permissions
./.devcontainer/fix-docker-permissions.sh

# Restart your session (log out/in or restart Cursor)
```

### 4. MethylUtils Not Found

**Error**: Import errors for MethylUtils packages

**Solution**:
```bash
# Install MethylUtils packages in the container
docker exec goliath /home/ubuntu/Work/cuda/setup_methyl_packages.sh

# Test the installation
docker exec goliath python -c "from methyl_utils.gpu_detection import print_gpu_status; print_gpu_status()"
```

### 5. Python Environment Issues

**Error**: Wrong Python interpreter or missing packages

**Solution**:
```bash
# Check Python path in container
docker exec goliath which python

# Install MethylDetector dependencies
docker exec goliath bash -c "cd /path/to/project && poetry install --with dev"
```

## Configuration Methods

### Method 1: Docker Compose (Default)
- **File**: `devcontainer.json`
- **Pros**: Uses existing Docker Compose setup, maintains all volume mounts
- **Cons**: May have path or profile issues
- **Best for**: When you want to use the exact same setup as the running container

### Method 2: Simple Image (Fallback)
- **File**: `devcontainer-simple.json` (rename to `devcontainer.json`)
- **Pros**: More reliable, simpler configuration
- **Cons**: May need to reconfigure some settings
- **Best for**: When Docker Compose method fails

## Quick Diagnostic Commands

```bash
# Test everything
./.devcontainer/test-devcontainer-connection.sh

# Test just Docker access
./.devcontainer/test-docker-access.sh

# Switch configuration method
./.devcontainer/switch-devcontainer-config.sh

# Check container status
docker ps | grep goliath

# Check Docker Compose config
docker compose -f /home/ubuntu/Work/cuda/docker-compose.yml config
```

## Step-by-Step Recovery

If nothing works, follow these steps:

1. **Ensure container is running**:
   ```bash
   cd /home/ubuntu/Work/cuda
   docker-compose down
   docker-compose up -d
   ```

2. **Switch to simple method**:
   ```bash
   ./.devcontainer/switch-devcontainer-config.sh
   # Choose option 2
   ```

3. **Test the setup**:
   ```bash
   ./.devcontainer/test-devcontainer-connection.sh
   ```

4. **Open Cursor and try again**:
   - Open the project folder
   - Click "Reopen in Container"

5. **If still failing, try manual attachment**:
   - In Cursor: "Remote-Containers: Attach to Running Container"
   - Select `goliath` container
   - Choose the project folder as workspace

## Still Having Issues?

If none of the above solutions work:

1. **Check the logs**: Look at Cursor's output panel for detailed error messages
2. **Try VS Code instead**: The devcontainer configuration should work in VS Code as well
3. **Use the container directly**: Access the container via `docker exec -it goliath bash`
4. **Check system resources**: Ensure you have enough memory and disk space

## File Structure Reference

```
.devcontainer/
├── devcontainer.json              # Main config (Docker Compose method)
├── devcontainer-simple.json       # Simple image method
├── devcontainer-compose.json      # Backup of Docker Compose method
├── README.modeler                     # Main documentation
├── TROUBLESHOOTING.modeler            # This file
├── DOCKER_PERMISSIONS_GUIDE.modeler   # Docker permission solutions
├── QUICK_START.modeler               # Quick start guide
├── test-devcontainer-connection.sh # Complete connection test
├── test-docker-access.sh        # Docker access test
├── switch-devcontainer-config.sh # Configuration switcher
└── fix-docker-permissions.sh    # Docker permissions fix
```
