---
name: make-commands
description: Reference for the shared `make` targets used across vivarium repositories (env setup, lint, test, docs, packaging, deploy, model lineage), centralized in `vivarium_build_utils/resources/makefiles/`. Use whenever the user asks what a `make <target>` does in a vivarium repo, mentions running `make` in this ecosystem, or whenever another skill or instruction says "run `make X`" and you need to know exactly what it does, its arguments, env vars, and side effects.
---

# Vivarium `make` commands

## How the system works

Every vivarium repo's top-level `Makefile` is a thin shim. In a local dev shell it imports two shared makefiles from the installed `vivarium_build_utils` package:

```make
include $(MAKE_INCLUDES)/base.mk
include $(MAKE_INCLUDES)/test.mk
```

Both files live at `vivarium_build_utils/resources/makefiles/`. That means **the same set of `make` targets is available in essentially every vivarium repo** (vivarium, vivarium_public_health, vivarium_inputs, risk_distributions, layered_config_tree, vivarium_cluster_tools, all the simulation repos, etc.). When you see `make <target>` in a vivarium context, look here.

The only target that lives in the *downstream* `Makefile` itself is `build-env`, because it has to bootstrap `vivarium_build_utils` into a fresh conda env before the shared `base.mk` can be loaded. Under Jenkins (`$JENKINS_URL` set), the makefiles are pulled from the workspace instead of from an installed package.

`PACKAGE_NAME` is automatically set to the current directory's basename, so the targets reorient themselves correctly per-repo.

## Quickly figuring out which target the user wants

`make` (no args) prints a generated list of every defined target with its inline `#` description. `make help` prints a curated, grouped help message. Both are worth pointing the user to when they're exploring — but when they ask in conversation, the table below should usually be enough.

## Diagnostic targets

| Target | What it does |
|---|---|
| `make list` (= `make` with no args) | Auto-generated list of every target whose definition has an inline `# description` comment. Pulled from the local `Makefile` and all `*.mk` files in `vivarium_build_utils/resources/makefiles/`. |
| `make help` | Curated, grouped help with descriptions of the diagnostic / helper / Jenkins-build target groups, piped through `less`. |
| `make debug` | Prints the currently-resolved values of the most important env vars (`PACKAGE_NAME`, `PACKAGE_VERSION`, `PYTHON_VERSION`, `CONDA_ENV_NAME`, `IHME_PYPI`, `LOCATIONS`, the installed `vivarium_build_utils` version). |

## Environment setup

| Target | What it does |
|---|---|
| `make build-env` | The normal way to start working in a vivarium repo. Creates a fresh conda env, `pip install`s `vivarium_build_utils` into it, then runs `make install` inside it. Defined in each downstream `Makefile` (not in `base.mk`) because it has to bootstrap `vivarium_build_utils` before the shared makefiles are even readable. Accepts two named args: `name=<env name>` (defaults to `PACKAGE_NAME`) and `py=<python version>` (defaults to the last version in `python_versions.json`, or 3.12 if that file is absent). After it finishes, `conda activate <name>` to use it. |
| `make create-env` | Creates a bare conda env with just Python. This is the Jenkins entry point and is normally not used directly in dev — `build-env` calls it via `conda create` and then bootstraps build-utils. Env is named `${PACKAGE_NAME}_py${PYTHON_VERSION}` by default; if `CONDA_ENV_PATH` is set (Jenkins), it uses `-p <path>` instead of `-n <name>`. |
| `make install` | Installs the current package and its dependencies (editable, with extras `[dev]` by default) using `uv pip install`, pulling from the IHME Artifactory PyPI as an extra index. Also runs `make setup-slack`. Accepts `ENV_REQS=<extras>` (default `dev`) to change which extras are installed, and `UV_FLAGS=...` for extra `uv pip` args. |
## Code quality

| Target | What it does |
|---|---|
| `make lint` | Runs `isort --check --diff` and `black --check --diff` against `$(LOCATIONS)` (default: `src tests`). **Does not modify files** — it only reports differences. Fails CI if either tool would have made changes. |
| `make format` | The "actually do the formatting" counterpart to `lint`. Runs `isort` and `black` against `$(LOCATIONS)`, modifying files in place. |
| `make mypy` | `mypy --config-file pyproject.toml .` Only meaningful for repos that opt in to typing (presence of `src/$(PACKAGE_NAME)/py.typed`). |
| `make check` | The CI-equivalent local check. Runs, in order: `lint`, `mypy` (only if `src/$(PACKAGE_NAME)/py.typed` exists), `test-all`, `build-docs`, `test-docs`. Use this before pushing to convince yourself CI will be green. |

## Tests (from `test.mk`)

| Target | What it does |
|---|---|
| `make test-all` | Runs `pytest -vvv` against the entire `tests/` directory with coverage, writing the HTML report to `./output/htmlcov_tests/`. |
| `make test-unit` | Same, but only `tests/unit/`. Coverage written to `./output/htmlcov_unit/`. |
| `make test-integration` | Same, but only `tests/integration/`. |
| `make test-e2e` | Same, but only `tests/e2e/`. |

All four targets respect two flags passed as `make` variables (not pytest CLI flags):
- `RUNSLOW=true` — adds `--runslow` to the pytest invocation, opting in to tests gated by the `slow` marker.
- `RUNWEEKLY=true` — adds `--runweekly`, opting in to tests gated by the `weekly` marker.

So `make test-all RUNSLOW=true` runs everything including slow tests; `make test-integration RUNWEEKLY=true` runs the weekly integration suite.

## Documentation

| Target | What it does |
|---|---|
| `make build-docs` | Runs `make html` inside `docs/` with strict Sphinx flags (`-T -W --keep-going`). No-op (prints "No 'docs/' folder found - skipping.") if `docs/` doesn't exist. Wipes `docs/build/` first. |
| `make test-docs` | Runs `make doctest` inside `docs/`. No-op if `docs/` doesn't exist. |

## Packaging and release

| Target | What it does |
|---|---|
| `make validate-tag` | Validates that the current git tag matches `CHANGELOG.rst` and is valid semver. Intended for use by GitHub deploy workflows — `bash $(UTILS_DIR)resources/scripts/validate_tag_version.sh`. |
| `make tag-version` | `git tag -a v${PACKAGE_VERSION} -m "..."` and `git push --tags`. `PACKAGE_VERSION` is parsed out of the first semver in `CHANGELOG.rst`. |
| `make build-package` | `pip install build && python -m build` — produces a wheel in `dist/`. |

## Misc

| Target | What it does |
|---|---|
| `make clean` | Removes `format`, `build-docs`, `build-package`, `integration`, `.pytest_cache`, `dist`, `output`, and all `*.pyc`/`*.pyo`/`__pycache__`. |
| `make model <subcommand> [args]` | Wrapper around the model lineage shell script (`vivarium_build_utils/resources/scripts/model_lineage.sh`), which analyzes git tag relationships for model-bearing repos. Subcommands include `list`, `base`, `contains`, `ancestors`, `check`, `matrix`, `tree`, `info`, `help`. Example: `make model tree`, `make model info v24.0`. |

## Important environment variables and arguments

These can be overridden from the command line (e.g. `make install ENV_REQS=test`) or exported in the shell:

| Variable | Default | Purpose |
|---|---|---|
| `PACKAGE_NAME` | basename of CWD | Drives env name, mypy gating, Artifactory deploy path. |
| `PYTHON_VERSION` | last entry in `python_versions.json` else `3.12` | Used by `create-env`/`build-env`. |
| `CONDA_ENV_NAME` | `${PACKAGE_NAME}_py${PYTHON_VERSION}` | Name passed to `conda create -n`. |
| `CONDA_ENV_PATH` | unset | If set (Jenkins), `conda create -p $CONDA_ENV_PATH` is used instead of `-n`. |
| `LOCATIONS` | `src tests` | Paths passed to isort/black for `lint` and `format`. |
| `ENV_REQS` | `dev` | Extras requested by `make install` (`pip install .[$ENV_REQS]`). |
| `UV_FLAGS` | empty | Extra args appended to the `uv pip install` line. |
| `IHME_PYPI` | `https://artifactory.ihme.washington.edu/artifactory/api/pypi/pypi-shared/` | Extra-index URL for installs; upload target for `deploy-package-artifactory`. |
| `PYPI_ARTIFACTORY_CREDENTIALS_USR` / `..._PSW` | unset | Required for `deploy-package-artifactory` / `manual-deploy-artifactory`. |
| `DOCS_ROOT_PATH` | unset | Required for `deploy-docs`. |
| `RUNSLOW` / `RUNWEEKLY` | unset | If set to a truthy value, opt-in flags are added to pytest. |

## Common workflows

**Starting fresh in a vivarium repo:**
```
make build-env                 # or:  make build-env name=my_env py=3.12
conda activate <env_name>
```

**Day-to-day before pushing:**
```
make format     # actually format
make check      # lint + (mypy if typed) + tests + docs + doctests
```

**Just run a subset of tests, slow ones included:**
```
make test-unit RUNSLOW=true
```

**Build docs locally:**
```
make build-docs    # output in docs/build/html
```

**Manual release (when Jenkins is down):**
```
export PYPI_ARTIFACTORY_CREDENTIALS_USR=...
export PYPI_ARTIFACTORY_CREDENTIALS_PSW=...
make manual-deploy-artifactory
```

**Inspect model lineage:**
```
make model tree
make model info v24.0
```

## Things to know that surprise people

- **`lint` doesn't fix anything.** It checks. To actually format, run `format`.
- **`make` with no args runs `list`**, not `help`. `.DEFAULT_GOAL := list` is set in `base.mk`.
- **`make install` re-runs `setup-slack` every time.** Idempotent, but expect to see Slack bot config output.
- **`test-*` targets read `RUNSLOW`/`RUNWEEKLY` as `make` variables**, not pytest CLI flags. `make test-all --runslow` will not work; `make test-all RUNSLOW=true` will.
- **`help`'s "Helper targets" group mentions `install-upstream-deps`**, but no target by that name is defined in `base.mk` or `test.mk`. Treat it as a stale entry in the help text rather than a real target until the user confirms otherwise.
- **Under Jenkins**, `MAKE_INCLUDES := .` — Jenkins copies `base.mk`/`test.mk` into the workspace ahead of time. In local dev, they're loaded from the installed `vivarium_build_utils` Python package via `get_makefiles_path()`.

## When the user asks "what does `make X` do?"

1. If `X` is in the tables above, summarize it from there — including any required env vars and meaningful side effects (git tagging/pushing, network uploads).
2. If it isn't, check the local `Makefile` in the user's CWD — downstream repos sometimes add repo-specific targets on top of the shared base.
3. If it still isn't there, read `vivarium_build_utils/resources/makefiles/base.mk` and `test.mk` directly — those are the source of truth and may have changed since this skill was written.
