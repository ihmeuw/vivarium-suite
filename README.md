# vivarium-suite

Monorepo for the Vivarium simulation framework and ecosystem libraries.

## Packages

| Directory | PyPI name | Import path |
|---|---|---|
| `libs/core/` | `vivarium-core` | `import vivarium.core` |
| `libs/public-health/` | `vivarium-public-health` | `import vivarium.public_health` |
| `libs/config-tree/` | `vivarium-config-tree` | `import vivarium.config_tree` |
| `libs/cluster-tools/` | `vivarium-cluster-tools` | `import vivarium.cluster_tools` |
| `libs/testing-utils/` | `vivarium-testing-utils` | `import vivarium.testing_utils` |
| `libs/helpers/` | `vivarium-helpers` | `import vivarium.helpers` |
| `libs/gbd-mapping/` | `vivarium-gbd-mapping` | `import vivarium.gbd_mapping` |
| `libs/risk-distributions/` | `vivarium-risk-distributions` | `import vivarium.risk_distributions` |
| `libs/profiling/` | `vivarium-profiling` | `import vivarium.profiling` |
| `libs/build-utils/` | `vivarium-build-utils` | `import vivarium.build_utils` |
| `libs/dependencies/` | `vivarium-dependencies` | *(meta-package)* |
| `libs/compat/` | `vivarium-compat` | *(import compatibility shim — temporary)* |

## Tools

Developer tooling that is not a Python package and is not published to PyPI lives under `tools/`.
These are not built or released by the monorepo's CI/release workflows.

| Directory | Purpose |
|---|---|
| `tools/ai-tools-public/` | Claude Code plugin (`simsci`): generic AI developer workflows for any IHME team (code review, git rescue, type hinting, regression debugging, guided TDD) |
| `tools/ai-tools/` | Claude Code plugin (`simsci-internal`): SimSci/vivarium-specific agent workflows (model development, team conventions, vivarium references); depends on `simsci` |

## Local development

Each package has its own development environment. From the package directory:

```bash
cd libs/core
make build-env name=vivarium-dev
conda activate vivarium-dev
```

To install a package into an already-active environment:

```bash
pip install -e "libs/core[dev]"
# or with uv:
uv pip install -e "libs/core[dev]"
```

CI uses [uv](https://docs.astral.sh/uv/) as the package manager.

## CI

- **Push/PR builds**: GitHub Actions (`.github/workflows/ci.yml`) — runs only for affected packages
- **Scheduled builds**: Jenkins — per-package Multibranch Pipelines provisioned by the top-level `Jenkinsfile`

## Releasing

Releases are triggered automatically when a `CHANGELOG.rst` is updated on `main`. A release can
also be triggered manually via `workflow_dispatch` on `.github/workflows/release.yml` (useful for
recovery or retries). See that file for details.
