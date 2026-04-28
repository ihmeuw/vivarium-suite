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
```

## CI

- **Push/PR builds**: GitHub Actions (`.github/workflows/ci.yml`) — runs only for affected packages
- **Scheduled builds**: Jenkins — per-package Multibranch Pipelines provisioned by the top-level `Jenkinsfile`

## Releasing

Releases are triggered automatically when a `CHANGELOG.rst` is updated on `main`. See `.github/workflows/release.yml`.
