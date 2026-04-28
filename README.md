# sim_sci_test_monorepo

A monorepo containing two Python libraries that share the `sim_sci_test_monorepo` namespace:

- `sim_sci_test_monorepo.core` - Core functionality and utilities
- `sim_sci_test_monorepo.public_health` - Public health specific functionality

## Structure

```
sim_sci_test_monorepo/
├── libs/
│   ├── core/                           # Core library
│   │   ├── src/
│   │   │   └── sim_sci_test_monorepo/
│   │   │       └── core/               # Core package
│   │   ├── tests/                      # Core tests
│   │   ├── pyproject.toml              # Core build configuration
│   │   └── README.md
│   └── public_health/                  # Public health library
│       ├── src/
│       │   └── sim_sci_test_monorepo/
│       │       └── public_health/      # Public health package
│       ├── tests/                      # Public health tests
│       ├── pyproject.toml              # Public health build configuration
│       └── README.md
├── pyproject.toml                      # Root configuration
├── Makefile                            # Development tasks
└── README.md
```

## Installation

### Prerequisites

This project uses [uv](https://github.com/astral-sh/uv) for fast Python package management. Install it first:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Development Installation

To install both libraries in development mode:

```bash
make install
```

Or install with development dependencies:

```bash
make install-dev
```

### Individual Libraries

You can also install libraries individually:

```bash
# Core library only
uv pip install -e libs/core

# Public health library only (includes core as dependency)
uv pip install -e libs/public_health
```

## Usage

Both libraries share the `sim_sci_test_monorepo` namespace:

```python
# Import from core
from sim_sci_test_monorepo.core.utils import hello_core, CoreUtility

# Import from public health
from sim_sci_test_monorepo.public_health.models import hello_public_health, HealthModel

# Example usage
print(hello_core())                    # Hello from sim_sci_test_monorepo.core!
print(hello_public_health())           # Hello from sim_sci_test_monorepo.public_health!

core_util = CoreUtility("example")
print(core_util.greet())               # Core utility example is ready!

health_model = HealthModel("flu_model", 10000)
print(health_model.simulate())         # Simulating flu_model for population of 10000
```

## Development

### Running Tests

```bash
# Run all tests
make test

# Run core tests only
make test-core

# Run public health tests only
make test-public-health
```

### Versioning

This project uses `setuptools_scm` for automatic versioning based on git tags. Each package has its own version tags:

- Core package: `core-v1.2.3`
- Public health package: `public-health-v1.2.3`

To check the current version:

```bash
make version
```

### Creating Releases

You can release each package independently:

#### Release Core Package
1. Use the GitHub Actions "release" workflow
2. Select "core" as the package
3. Provide a version number (e.g., `1.2.3`)
4. This creates a `core-v1.2.3` tag and triggers deployment

#### Release Public Health Package  
1. Use the GitHub Actions "release" workflow
2. Select "public_health" as the package
3. Provide a version number (e.g., `1.2.3`)
4. This creates a `public-health-v1.2.3` tag and triggers deployment

The deploy workflow automatically triggers when releases are published.

### Code Formatting and Linting

```bash
# Format code
make format

# Lint code
make lint
```

### Cleaning Build Artifacts

```bash
make clean
```

## Package Structure

The libraries use Python namespace packages, allowing them to be:
- Developed and distributed separately
- Installed independently or together
- Share the same top-level namespace (`sim_sci_test_monorepo`)

Each library has its own:
- `pyproject.toml` for build configuration and dependencies
- Test suite
- Documentation
- Version management
