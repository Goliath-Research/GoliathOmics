#!/bin/bash

# MethylClassifier Repository Setup Script
# This script sets up the new MethylClassifier repository in Azure DevOps

set -e  # Exit on any error

echo "🚀 Setting up MethylClassifier repository in Azure DevOps"
echo "========================================================="

# Check if Azure CLI is installed
if ! command -v az &> /dev/null; then
    echo "❌ Azure CLI is not installed. Please install it first."
    exit 1
fi

# Check if already logged in
if ! az account show &> /dev/null; then
    echo "🔐 You need to login to Azure CLI first..."
    echo "Opening browser for authentication..."
    az login
fi

echo "✅ Azure CLI authenticated successfully"

# Create the repository
echo "📝 Creating MethylClassifier repository..."
REPO_RESULT=$(az repos create \
    --organization https://github.com/Goliath-Research \
    --project Development \
    --name MethylClassifier \
    --open-source true \
    --output json)

if [ $? -eq 0 ]; then
    echo "✅ Repository created successfully!"

    # Extract repository information
    REPO_URL=$(echo $REPO_RESULT | jq -r '.sshUrl')
    REPO_ID=$(echo $REPO_RESULT | jq -r '.id')

    echo "📋 Repository Details:"
    echo "   Name: MethylClassifier"
    echo "   ID: $REPO_ID"
    echo "   SSH URL: $REPO_URL"
else
    echo "❌ Failed to create repository"
    exit 1
fi

# Initialize git repository
echo "🔧 Setting up local git repository..."

cd /home/ubuntu/MethylClassifier

# Initialize git if not already done
if [ ! -d ".git" ]; then
    git init
    echo "✅ Git repository initialized"
else
    echo "ℹ️  Git repository already initialized"
fi

# Configure git user (same as MethylUtils)
git config user.name "David Izada Rodriguez"
git config user.email "davidizadar@goliathresearch.com"
echo "✅ Git user configured"

# Add all files
git add .
echo "✅ Files staged for commit"

# Initial commit
git commit -m "Initial commit: MethylClassifier CLI application

- Standalone command-line tool for methylation-based sample classification
- Supports Bayesian classification with multiple centroids
- Modular architecture with abstract data loading
- Compatible with existing methyl_utils classifiers
- Comprehensive CLI with detailed output and debugging"
echo "✅ Initial commit created"

# Add remote
git remote add origin "$REPO_URL"
echo "✅ Remote origin added"

# Push to Azure DevOps
echo "📤 Pushing to Azure DevOps..."
git push -u origin main

if [ $? -eq 0 ]; then
    echo ""
    echo "🎉 SUCCESS! MethylClassifier repository is now live!"
    echo ""
    echo "📍 Repository URL: https://github.com/Goliath-Research/Development/_git/MethylClassifier"
    echo "🔗 SSH Clone URL: $REPO_URL"
    echo ""
    echo "📋 Next steps:"
    echo "   1. Visit the repository URL above to see your code"
    echo "   2. Set up any necessary pipelines or policies"
    echo "   3. Consider creating a release branch for production deployments"
    echo ""
else
    echo "❌ Failed to push to repository"
    echo "💡 You may need to:"
    echo "   - Check your SSH keys are configured correctly"
    echo "   - Manually push using: git push -u origin main"
    exit 1
fi
