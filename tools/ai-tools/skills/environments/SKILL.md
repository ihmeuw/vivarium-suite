---
name: environments
description: How to pick up the right Python environment in a vivarium repo, and how to create one if you need to. Use whenever you need to run tests, lint, scripts, or execute make commands, anything that imports the package. Also use when the user asks to "set up an environment", "use the shared env", "rebuild my env", or hits import/activation problems.
---

# Vivarium environments

## You almost always need an environment!

Running tests, linting, importing the package, executing scripts — all of it needs an active environment.

## Discovering the right environment

Stop as soon as you have a confident match:

1. **VS Code settings.** Check the interpreter for the current workspace in VS Code.
2. **Conda env list.** Run `conda env list` and look for envs that either match the repo / monorepo library name or seem to be a shortened version thereof, e.g. 'vct' for 'vivarium_cluster_tools'. If one seems borderline, confirm with the user. 
3. **`.venv/` directory.** Particuarly if you are working in a model repository, look for `.venv/<repo>_<type>/` (where type can be 'simulation' or 'artifact'). That's a shared-env venv overlay (see below).
5. **Ask the user** if multiple plausible candidates exist (e.g. both `simulation` and `artifact` are present and the task doesn't make the choice obvious). Put the answer in your memory.

Don't silently `conda create` a new env to "make the problem go away" — that ends with stale parallel envs and missed bugs.

## Creating one when none exists

You can create a new local environment using `make build-env`.

If you are on the IHME cluster and working on a model repository, you can also use `make build-shared-env` to create a lightweight venv overlay on top of a shared env. See the repository's Makefile for details. When available, this is typically the preferred option.

Some model repositories additionally have an `environment.sh` script that will activate an environment, rebuilding if necessary. If you are working in one of those, use it instead of the above commands. You can inspect the file for details.
