#!/bin/bash

# Setup script for sim_sci_test_monorepo

set -e

echo "Setting up sim_sci_test_monorepo development environment..."

# Check if we're in a virtual environment
if [[ "$VIRTUAL_ENV" != "" ]]; then
    echo "✓ Virtual environment detected: $VIRTUAL_ENV"
else
    echo "⚠️  No virtual environment detected. Consider using 'python -m venv venv && source venv/bin/activate'"
    echo "   Or use 'uv venv' to create a virtual environment with uv"
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "❌ uv is not installed. Please install it first:"
    echo "   curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo "   Or visit: https://github.com/astral-sh/uv"
    exit 1
fi

# Install development dependencies and libraries
echo "Installing development dependencies..."
make install-dev

# Run tests to ensure everything is working
echo "Running tests..."
make test

echo ""
echo "✓ Setup complete!"
echo ""
echo "You can now:"
echo "  - Import from sim_sci_test_monorepo.core"
echo "  - Import from sim_sci_test_monorepo.public_health"
echo "  - Run 'make help' to see available development commands"