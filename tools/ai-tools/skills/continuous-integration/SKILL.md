---
name: continuous-integration
description: Use when the user asks about vivarium-suite CI — build status, Jenkins console logs, job structure, GitHub Actions runs, etc. Covers the GH Actions / Jenkins setup, the per-package Multibranch Pipeline layout, URL-to-jobFullName translation for the Jenkins MCP, and parallel matrix log interleaving.
---

# Continuous integration

Vivarium-suite runs two parallel CI systems for each `libs/<pkg>` change. This skill catalogues the layout of both, plus the operational details needed to navigate Jenkins via the Jenkins MCP server.

## CI systems

- **GitHub Actions** (`.github/workflows/ci.yml`) runs on push/PR for *affected* packages only. The `detect-changes` job diffs against the base ref and builds a `{library, python-version}` matrix from each changed lib's `python_versions.json`. Root-level changes (`pyproject.toml`, `Makefile`, workflows) trigger a full rebuild. GH runners install `vivarium_build_utils` from git, then run `make install ENV_REQS=ci_github UV_FLAGS=--system IHME_PYPI=`. `IHME_PYPI=` disables the artifactory extra-index because GH runners cannot reach IHME's firewalled artifactory; packages with artifactory-only dependencies are only fully exercised in Jenkins.
- **Jenkins** (`jenkins.simsci.ihme.washington.edu`) is provisioned by the top-level `Jenkinsfile`, which calls `monorepo(...)` from `vivarium_build_utils` to auto-create one Multibranch Pipeline per `libs/*/Jenkinsfile`. Adding a new package under `libs/` is picked up on the next main build. Each lib's Jenkinsfile delegates to `reusable_pipeline(...)` from the same shared library.

## Vivarium-suite Jenkins job layout

The `monorepo(...)` call described above produces this folder/job hierarchy on Jenkins:

- Folder: `Public/vivarium-suite/libs/`
- Multibranch project per package: `Public/vivarium-suite/libs/<pkg>` (e.g. `.../profiling`, `.../compat`)
- Branch sub-job: `Public/vivarium-suite/libs/<pkg>/<branch>` — this is the `jobFullName` to pass to MCP tools (default branch is usually `main`)

## Translating a Jenkins URL to a `jobFullName`

The Jenkins MCP tools take a `jobFullName` parameter (with `/` separators between folder/job segments) plus a separate `buildNumber`. Translate from a console URL by stripping the leading `/job/`, replacing every `/job/` with `/`, and dropping everything from the build number onward:

```
URL:          .../job/Public/job/vivarium-suite/job/libs/job/profiling/job/main/3/console
jobFullName:  Public/vivarium-suite/libs/profiling/main
buildNumber:  3
```

## Parallel matrix output is interleaved in a single console log

`reusable_pipeline` runs each python version as a parallel branch within a single build, so one console log carries output from all matrix axes intermixed. `searchBuildLog` hits are not partitioned by axis. Two reliable disambiguation signals:

- Parallel stage entries are marked `[Pipeline] { (<Stage Name> - Python X.Y)` — searching this pattern first gives a stage map that lines from later searches can be cross-referenced against by line number.
- Conda environment paths in the log carry the matrix axis as a suffix (e.g. `.../envs/.../<branch>-<build>-3.10` vs `.../<branch>-<build>-3.11`). Any log line containing one of these paths is unambiguous.
