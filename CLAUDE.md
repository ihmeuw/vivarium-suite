# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Code Style

On our team, we type-hint all new python code. We generally use NumPy Doc style, with the exception that we do not add types in the docstrings for parameters or returns, since they are given by the type hinting. Don't add a return section if the function returns None. Always add a full docstring to new public methods and functions, but for private methods and functions a single line is usually sufficient. Docstrings should be in the imperative mood.

### Docstrings and comments — short by default

These guard against verbosity. Prefer fewer words; a reader should never have to skim past filler to reach the point.

- **Docstrings start with the one-line imperative summary, and for most functions that is the whole docstring.**
- **No boilerplate openers.** Don't begin with "This function…", "This method…", or "A helper that…". Lead with the verb: "Compute…", "Return…", "Validate…".
- **Comment on *why*, not *what*.** Write a comment only when the code can't speak for itself — a non-obvious reason, a workaround, an invariant, a reference. Default to none.
- **Never narrate the code** (`# loop over the rows`, `# increment the counter`). If a comment restates the line below it, delete the comment; if the code is confusing, prefer a clearer name or a small refactor over a comment that explains it.

## Repository shape

Monorepo for the Vivarium framework. Each subdirectory under `libs/` is an independently versioned and released Python package; the root itself is *not* a Python package. The root `pyproject.toml` only holds shared lint config (`black`, `isort`, `mypy`). Almost all work happens inside one `libs/<pkg>/` directory.

Packages and their import paths are listed in `README.md`. The user-facing namespace is `vivarium.<subpackage>` (e.g. `vivarium.profiling`, `vivarium.config_tree`); the PyPI names are dash-form (e.g. `vivarium-profiling`).

## Working in a package

Always `cd libs/<pkg>` before running make targets - they read `python_versions.json` and `pyproject.toml` from the cwd.

```bash
cd libs/<pkg>
make build-env name=<env-name>          # create conda env (uses latest supported py by default)
conda activate <env-name>
make install ENV_REQS=dev               # install editable + dev extras into active env
make lint                               # black + isort
make mypy                               # only if src/ contains a py.typed marker
make test-all                           # full test suite
make build-docs                         # sphinx docs
make build-package                      # build wheel/sdist into dist/
make validate-tag                       # used by release CI; checks tag matches CHANGELOG
```

`make help` (inside a package, after `build-env`) lists everything else. Most targets come from the `vivarium.build_utils` package's `base.mk` / `test.mk`, which the local `Makefile` includes dynamically via `python -c "from vivarium.build_utils.resources import get_makefiles_path"`. Outside an env where `vivarium.build_utils` is installed, only `build-env` is available; this is by design.

To run a single test, activate the env and use pytest directly: `pytest tests/path/to/test_foo.py::test_name -xvs`.

## Verifying working state

Before any externally-facing action (pushing, opening a PR, tagging a release), run `make check` from inside `libs/<pkg>`. It is the canonical pre-flight: it runs `lint`, `mypy` (when the package has a `py.typed` marker), the fast test suite, and the docs build + doctests - so it also validates that docstring/doc cross-references still resolve under Sphinx's warnings-as-errors mode. It no-ops the docs steps for packages without a `docs/` directory, so the same command is safe in every package. Because it is comprehensive it is slow; iterate with targeted `pytest`/`make lint` and use `make check` as the final gate. This is a multi-step operation that streams significant output and takes tens of seconds, so run this check in the background.

## Per-package conventions

- Every package needs a `python_versions.json` (a JSON array of strings like `["3.10", "3.11"]`). CI fans out the test matrix from this file.
- A `py.typed` marker file in `src/` gates whether CI runs `mypy` on the package.
- Each package keeps its own `CHANGELOG.rst`, `Jenkinsfile`, `pyproject.toml`, and `Makefile`.
- `setuptools_scm` is configured per-package with a `tag_regex` of `vivarium-<pkg>-v<X.Y.Z>` and `root = "../.."` so it resolves against the monorepo git history.

## Release model

Releases (`.github/workflows/release.yml`) fire when a `libs/<pkg>/CHANGELOG.rst` is touched on `main`. The workflow:

1. Parses the version from the first CHANGELOG line - format is `**X.Y.Z - MM/DD/YY**` (2-digit year, Pacific date matching the push day).
2. Creates and pushes a `vivarium-<pkg>-v<X.Y.Z>` tag.
3. Runs `make validate-tag` with `TAG_PREFIX=vivarium-<pkg>-` so the validator strips the per-lib prefix before semver parsing and scopes its "previous tag" lookup to that lib only.
4. Builds and publishes to PyPI, then creates a GitHub Release.

`workflow_dispatch` and `release: published` paths exist for manual/recovery releases of a specific lib.

The Claude Code plugins are *not* PyPI packages, so each has its own tag-and-release
workflow: `tools/ai-tools` (the `simsci-internal` plugin) releases via
`.github/workflows/release-ai-tools.yml` (tag `vivarium-ai-tools-v<X.Y.Z>`), and
`tools/ai-tools-public` (the `simsci` plugin, the generic subset any IHME team can
install) via `.github/workflows/release-ai-tools-public.yml` (tag
`vivarium-ai-tools-public-v<X.Y.Z>`). Each fires when its plugin's `CHANGELOG.rst` is
touched on `main` and parses the version from the same `**X.Y.Z - MM/DD/YY**` first line —
no build, test, or PyPI publish. They are kept separate from `release.yml` so plugin
releases never touch that workflow's PyPI trusted-publishing credential path (its
`id-token: write` permission).

## Note on packaging

`libs/<pkg>/pyproject.toml` deliberately uses `include = ["vivarium.<pkg>", "vivarium.<pkg>.*"]` so the wheel ships only the `vivarium/<pkg>/` subtree and *not* `vivarium/__init__.py`. The canonical `vivarium/__init__.py` is owned by `vivarium-engine`; shipping our own would clobber it at install time (two distributions writing the same file). Apply this same pattern to any other package that lives under the `vivarium.*` namespace.
