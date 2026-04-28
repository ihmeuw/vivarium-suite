.PHONY: install install-dev test test-core test-public-health format lint clean version help

# Default target
help:
	@echo "Available targets:"
	@echo "  install          - Install both libraries in development mode"
	@echo "  install-dev      - Install development dependencies"
	@echo "  test            - Run all tests"
	@echo "  test-core       - Run core library tests"
	@echo "  test-public-health - Run public health library tests"
	@echo "  format          - Format code with black and isort"
	@echo "  lint            - Run linting with flake8"
	@echo "  version         - Show current versions (requires git tags)"
	@echo "  clean           - Clean build artifacts"

# Install both libraries in development mode
install:
	uv pip install -e libs/core
	uv pip install -e libs/public_health

# Install development dependencies
install-dev:
	uv pip install -e "libs/core[dev]"
	uv pip install -e "libs/public_health[dev]"

# Run all tests
test:
	pytest libs/core/tests libs/public_health/tests -v

# Run core tests only
test-core:
	pytest libs/core/tests -v

# Run public health tests only
test-public-health:
	pytest libs/public_health/tests -v

# Run config tree tests only
test-config-tree:
	pytest libs/config_tree/tests -v

# Run integration tests
test-integration:
	python -c "from sim_sci_test_monorepo.core.utils import hello_core, CoreUtility; from sim_sci_test_monorepo.public_health.models import hello_public_health, HealthModel; print('✓ Integration test passed')"

# Format code
format:
	black libs/
	isort libs/

# Lint code
lint:
	flake8 libs/

# Show current versions
version:
	@echo "Checking versions with setuptools_scm..."
	@cd libs/core && python -c "import setuptools_scm; print('Core version:', setuptools_scm.get_version())" 2>/dev/null || echo "Core: setuptools_scm not available, install with 'make install-dev'"
	@cd libs/public_health && python -c "import setuptools_scm; print('Public Health version:', setuptools_scm.get_version())" 2>/dev/null || echo "Public Health: setuptools_scm not available, install with 'make install-dev'"

# Build both packages
build: build-core build-public-health

# Build core package only
build-core:
	cd libs/core && python -m build

# Build public health package only
build-public-health:
	cd libs/public_health && python -m build

# Clean build artifacts
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "build" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "dist" -exec rm -rf {} + 2>/dev/null || true