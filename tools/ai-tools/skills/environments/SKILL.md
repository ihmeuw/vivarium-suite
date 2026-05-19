---
name: environments
description: How to pick up the right Python environment in a vivarium repo, and how to create one if you need to. Use whenever you need to run tests, lint, scripts, or anything that imports the package — basically every non-trivial task. Also use when the user asks to "set up an environment", "use the shared env", "rebuild my env", or hits import/activation problems.
---

# Vivarium environments

## You almost always need one

Running tests, linting, importing the package, executing scripts — all of it needs an active environment. Before running anything substantial, confirm one is active (`echo $CONDA_DEFAULT_ENV $VIRTUAL_ENV`). If neither is set, work through discovery before you start creating.

## Discovering the right environment

In order — stop as soon as you have a confident match:

1. **VS Code settings.** Check `.vscode/settings.json` for `python.defaultInterpreterPath` (or the workspace's `.code-workspace`). That's the interpreter the user actually develops against, so it's the right default.
2. **Conda env list.** Run `conda env list` and look for `<repo-dir-basename>_simulation` or `<repo-dir-basename>_artifact` — the convention every vivarium repo follows. If exactly one matches, use it.
3. **`.venv/` directory.** Look for `.venv/<repo>_<type>/`. That's a shared-env venv overlay (see below); activate with `source .venv/<name>/bin/activate`.
4. **`environment.sh`.** If the repo ships one (today only `vivarium_gates_mncnh`), `source environment.sh -h` describes the options and the script handles discovery + activation in one shot.
5. **Ask the user** if multiple plausible candidates exist (e.g. both `simulation` and `artifact` are present and the task doesn't make the choice obvious).

Don't silently `conda create` a new env to "make the problem go away" — that ends with stale parallel envs and missed bugs.

## Creating one when none exists

| Mode | Make target | Code lives in | Use when |
|---|---|---|---|
| **Full conda env** | `make build-env` | The repo's own top-level `Makefile` (only target that has to live there — it bootstraps `vivarium_build_utils` into a fresh env before VBU's shared makefiles can be loaded) | You're off-cluster, or you need non-Python conda packages |
| **Shared-env venv overlay** | `make build-shared-env` | The repo's own top-level `Makefile` | You're on the cluster and want fast iteration with deps already built |
| **One-shot wrapper** | `source environment.sh` | The repo (only `vivarium_gates_mncnh` today) | The repo ships it and you want a single command that picks/builds/activates |

Each target documents its args in `make help` and validates them at invocation time. The shared envs the overlay attaches to are built by **`vivarium_build_utils/Jenkinsfile.shared-env`** — read that file when the user asks "where does the shared env come from" or "how often is it rebuilt".

## Shared vs local — which to use

**Use the shared-env overlay (`build-shared-env`) when:**
- You're on the IHME cluster and `/mnt/team/simulation_science/priv/engineering/jenkins/shared_envs/` is reachable.
- You want fast setup — the heavy deps are already installed; only the local repo is pip-installed editable.
- You're fine tracking the shared env's version of every dep (Jenkins rebuilds nightly-ish; pinned by whatever `main` resolved to at build time).

**Use a full local conda env (`build-env`) when:**
- You're off-cluster.
- You need a non-Python conda package the shared env doesn't have.
- You need to pin or experiment with a dep version that differs from the shared env.
- You're debugging something that might be caused by the shared env itself.

When in doubt and the user is on-cluster, default to shared. It's faster, and downstream environment-skew bugs are rarer because everyone on the cluster is overlaying the same base.
