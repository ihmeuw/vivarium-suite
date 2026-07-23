# vivarium-suite

Monorepo for the Vivarium simulation framework and ecosystem libraries.

## Packages

| Directory | PyPI name | Import path |
|---|---|---|
| `libs/artifact/` | `vivarium-artifact` | `import vivarium.artifact` |
| `libs/build-utils/` | `vivarium-build-utils` | `import vivarium.build_utils` |
| `libs/cluster-tools/` | `vivarium-cluster-tools` | `import vivarium.cluster_tools` |
| `libs/config-tree/` | `vivarium-config-tree` | `import vivarium.config_tree` |
| `libs/dependencies/` | `vivarium-dependencies` | *(meta-package)* |
| `libs/engine/` | `vivarium-engine` | `import vivarium.engine` |
| `libs/fuzzy-checker/` | `vivarium-fuzzy-checker` | `import vivarium.fuzzy_checker` |
| `libs/gbd-mapping/` | `vivarium-gbd-mapping` | `import vivarium.gbd_mapping` |
| `libs/profiling/` | `vivarium-profiling` | `import vivarium.profiling` |
| `libs/public-health/` | `vivarium-public-health` | `import vivarium.public_health` |
| `libs/pytest-vivarium/` | `pytest-vivarium` | *(pytest plugin — auto-loaded)* |
| `libs/risk-distributions/` | `vivarium-risk-distributions` | `import vivarium.risk_distributions` |
| `libs/validation/` | `vivarium-validation` | `import vivarium.validation` |

## Tools

Developer tooling that is not a Python package and is not published to PyPI lives under `tools/`.
These are not built or released by the monorepo's CI/release workflows.

| Directory | Purpose |
|---|---|
| `tools/ai-tools/` | Claude Code plugin: custom agent workflows for vivarium development (code review, regression debugging) |

## Local development

Each package has its own development environment. From the package directory:

```bash
cd libs/engine
make build-env name=vivarium-dev
conda activate vivarium-dev
```

To install a package into an already-active environment:

```bash
pip install -e "libs/engine[dev]"
# or with uv:
uv pip install -e "libs/engine[dev]"
```

CI uses [uv](https://docs.astral.sh/uv/) as the package manager.

## CI

- **Push/PR builds**: GitHub Actions (`.github/workflows/ci.yml`) — runs only for affected packages
- **Scheduled builds**: Jenkins — per-package Multibranch Pipelines provisioned by the top-level `Jenkinsfile`

## Releasing

Releases are triggered automatically when a `CHANGELOG.rst` is updated on `main`. A release can
also be triggered manually via `workflow_dispatch` on `.github/workflows/release.yml` (useful for
recovery or retries). See that file for details.
