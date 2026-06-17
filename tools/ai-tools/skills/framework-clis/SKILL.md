---
name: framework-clis
description: Reference for the vivarium-ecosystem console scripts that a standard model-repo conda env puts on PATH — `simulate`, `psimulate`, `vipin`, the per-repo `make_artifacts`, and `update_gbd_round`. Use whenever the task involves running, restarting, expanding, or smoke-testing a vivarium simulation; launching one on the cluster; building or rebuilding a model's data artifact; extracting psimulate worker performance; or exporting a GBD round.
---

# Vivarium framework CLIs

## What this skill covers

The vivarium-ecosystem console scripts that a standard model-repo conda env (the one `make build-env type=simulation` produces in a `vivarium_gates_*` or similar repo) puts on PATH.

| CLI | Ships in | Entry point | What it does |
|---|---|---|---|
| `simulate` | `vivarium-engine` (the `vivarium` PyPI name is a shim that pulls it in) | `vivarium.engine.interface.cli:simulate` | Run one simulation locally from a model spec YAML |
| `psimulate` | `vivarium_cluster_tools` | `vivarium_cluster_tools.psimulate.cli:psimulate` | Run many simulations in parallel on the IHME cluster |
| `vipin` | `vivarium_cluster_tools` | `vivarium_cluster_tools.vipin.cli:vipin` | Extract per-worker perf info from a finished `psimulate` results dir |
| `make_artifacts` | the *current* model repo | `<pkg>.tools.cli:make_artifacts` | Build the HDF5 data artifact for *this* model — signature differs per repo |
| `update_gbd_round` | `vivarium_inputs` | `vivarium_inputs.round_updates.cli:update_gbd_round` | Export GBD entities/measures to CSV from a YAML config; can fan out as cluster jobs |

## How to discover more detailed information

Two stable sources, in order:
1. `<cli> --help` and `<cli> <subcommand> --help`
2. The entry-point module path in the at-a-glance table — read the source when behavior is ambiguous or the help text is terse.

If a user asks about a vivarium-shipped CLI that isn't in the table above, treat it as out of scope for this skill and fall back to those two sources.

## Notes

### `make_artifacts` is per-repo

There is no single shared `make_artifacts` command. **Every model repo ships its own**, registered as `console_scripts:make_artifacts = <that_repo_pkg>.tools.cli:make_artifacts`. Whichever model package's env is active is the one whose `make_artifacts` is on PATH.