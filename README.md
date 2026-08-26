# vivarium-suite

Monorepo for the Vivarium simulation framework and ecosystem libraries.

## Packages

| Directory | PyPI name | Import path | Documentation |
|---|---|---|---|
| `libs/artifact/` | `vivarium-artifact` | `import vivarium.artifact` | https://vivarium-artifact.readthedocs.io |
| `libs/build-utils/` | `vivarium-build-utils` | `import vivarium.build_utils` | *(no docs)* |
| `libs/cluster-tools/` | `vivarium-cluster-tools` | `import vivarium.cluster_tools` | https://vivarium-cluster-tools.readthedocs.io |
| `libs/config-tree/` | `vivarium-config-tree` | `import vivarium.config_tree` | https://vivarium-config-tree.readthedocs.io |
| `libs/dependencies/` | `vivarium-dependencies` | *(meta-package)* | *(no docs)* |
| `libs/engine/` | `vivarium-engine` | `import vivarium.engine` | https://vivarium-engine.readthedocs.io |
| `libs/fuzzy-checker/` | `vivarium-fuzzy-checker` | `import vivarium.fuzzy_checker` | https://vivarium-fuzzy-checker.readthedocs.io |
| `libs/gbd-mapping/` | `vivarium-gbd-mapping` | `import vivarium.gbd_mapping` | https://vivarium-gbd-mapping.readthedocs.io |
| `libs/profiling/` | `vivarium-profiling` | `import vivarium.profiling` | *(no docs)* |
| `libs/public-health/` | `vivarium-public-health` | `import vivarium.public_health` | https://vivarium-public-health.readthedocs.io |
| `libs/pytest-vivarium/` | `pytest-vivarium` | *(pytest plugin — auto-loaded)* | https://pytest-vivarium.readthedocs.io |
| `libs/risk-distributions/` | `vivarium-risk-distributions` | `import vivarium.risk_distributions` | https://vivarium-risk-distributions.readthedocs.io |
| `libs/validation/` | `vivarium-validation` | `import vivarium.validation` | https://vivarium-validation.readthedocs.io |

## Tools

Developer tooling that is not a Python package and is not published to PyPI lives under `tools/`.
These are not built or released by the monorepo's CI/release workflows.

| Directory | Purpose |
|---|---|
| `tools/ai-tools-public/` | Claude Code plugin (`simsci`): generic AI developer workflows for any IHME team (code review, git rescue, type hinting, regression debugging, guided TDD) |
| `tools/ai-tools/` | Claude Code plugin (`simsci-internal`): SimSci/vivarium-specific agent workflows (model development, team conventions, vivarium references); depends on `simsci` |
| `tools/model-template/` | Cookiecutter template for producing research model repositories |

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
