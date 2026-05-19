---
name: environments
description: How to create, pick up, switch between, and rebuild Python environments in a vivarium repo — covers `make build-env` (full conda env), `make build-shared-env` (venv overlay on a Jenkins-built shared conda env), the Jenkins shared-env infrastructure in `vivarium_build_utils`, and the `environment.sh` convenience script (currently only in `vivarium_gates_mncnh`). Use whenever the user asks to "set up an environment", "create a conda env", "use the shared env", "rebuild my env", "switch to artifact env", "what's in `environment.sh`", "is my env stale", or hits problems activating/installing into a vivarium env.
---

# Vivarium environments

## The three ways to get into an environment

| Mode | Mechanism | Use when |
|---|---|---|
| **Full conda env** | `make build-env` | You can't use the shared env (off-cluster, custom non-python deps needed) |
| **Shared-env venv overlay** | `make build-shared-env` | You're on the cluster and just want fast iteration on the local repo |
| **`source environment.sh`** | Wraps both, plus staleness check | You're in a repo that ships it (today: only `vivarium_gates_mncnh`) and want a one-liner |

All three are surfaced by `make help` in any repo that has them. Run that first if you're not sure which the user wants.

---

## `make build-env` — full conda environment

Lives in the **per-repo `Makefile`** (not VBU's `base.mk`), because it has to bootstrap `vivarium_build_utils` into a fresh env before the shared makefiles can be loaded. Every vivarium repo defines its own `build-env`, but they're near-identical — `mncnh`'s is the canonical reference.

### Usage

```bash
make build-env [type=...] [name=...] [path=...] [py=...] [lfs=...] [force=...] [include_timestamp=...]
```

| Arg | Default | Meaning |
|---|---|---|
| `type` | `simulation` | `simulation` → installs `[dev]` extras + adds `redis` via conda; `artifact` → installs `[data]` extras |
| `name` | `<PACKAGE_NAME>_<type>` | Conda env name; created with `conda create -n` |
| `path` | (unset) | Absolute path; if set, env is created with `conda create -p` instead of `-n`. Used by Jenkins shared-env builds |
| `py` | Last entry in `python_versions.json` | Python version |
| `lfs` | `no` | If `yes`, installs `git-lfs` via conda-forge and runs `git lfs install` in the env |
| `force` | `no` | If env exists and `force=yes`, removes it first; otherwise the target errors out |
| `include_timestamp` | `no` | Appends `_YYYYMMDD_HHMMSS` to `name`. Used so Jenkins archives are uniquely tagged |

After build, `make install` re-runs `setup-slack`, which copies `/mnt/team/simulation_science/priv/engineering/config/slack_bot_config.sh` into `$CONDA_PREFIX/etc/conda/activate.d/` so `PSIMULATE_SLACK_BOT_TOKEN` gets exported on `conda activate`. Expect the "Slack bot token configured" message on every install.

### Activating

```bash
conda activate <name>          # if name was used
conda activate <path>          # if path was used
```

---

## `make build-shared-env` — venv overlay on a shared conda env

Also lives in the per-repo `Makefile`. Creates a lightweight `venv` on top of a Jenkins-built shared conda env. **This is the recommended path for cluster development** — you get the heavy deps from the shared env without the cost of rebuilding them locally, plus an editable install of the current repo.

### Usage

```bash
make build-shared-env [type=...] [venv_dir=.venv] [venv_name=<pkg>_<type>] [shared_env_dir=...] [force=...]
```

| Arg | Default | Meaning |
|---|---|---|
| `type` | `simulation` | Which shared env to overlay (`simulation` or `artifact`) |
| `venv_dir` | `.venv` | Where venvs are stored under the repo |
| `venv_name` | `<PACKAGE_NAME>_<type>` | Subdir name under `venv_dir` |
| `shared_env_dir` | `/mnt/team/simulation_science/priv/engineering/jenkins/shared_envs` | Base dir for shared envs (rarely changed) |
| `force` | `no` | Recreate if the venv already exists |

### How it works

1. Requires the shared env to already exist at `<shared_env_dir>/<PACKAGE_NAME>_<type>_current`. If not, the target errors with "Make sure the Jenkins nightly build has run successfully."
2. Creates `<venv_dir>/<venv_name>` via `python -m venv --system-site-packages` using the shared env's Python.
3. **Patches the venv's `activate` script** to set `PATH="$VIRTUAL_ENV/bin:<shared_env_path>/bin:$_OLD_VIRTUAL_PATH"`. This makes CLI entry points from the shared env (e.g. `psimulate`) resolve correctly while still letting the venv's bin win. Same patch applied to `activate.fish` and `activate.csh` if present.
4. Installs the local repo with `pip install -e . --no-deps` (no-deps since the shared env already provides them).

### Activating

```bash
source <venv_dir>/<venv_name>/bin/activate
# e.g. source .venv/vivarium_gates_mncnh_simulation/bin/activate
```

---

## How shared envs get built (Jenkins infrastructure)

The shared envs at `/mnt/team/simulation_science/priv/engineering/jenkins/shared_envs/` are produced by a Jenkins pipeline defined in **`vivarium_build_utils/Jenkinsfile.shared-env`**. Read that file when the user asks "where does the shared env come from" or "how often is it rebuilt".

Key facts (as of writing — re-read the Jenkinsfile to confirm):

- **Default models**: `vivarium_gates_mncnh` (parameter `ACTIVE_MODELS`, comma-separated; expand here when other models adopt shared envs).
- **Default types**: `simulation,artifact` (parameter `ENV_TYPES`).
- **Per env, per build, sequentially** the pipeline does:
  1. `conda-pack` the existing `<name>_current` env into `archive/<env_name>/<env_name>_<original-build-timestamp>.tar.gz`.
  2. `rm -rf` the old env at its `_current` path.
  3. Re-clone the model repo at `main`, run `make build-env type=<type> name=<name>_current path=<...>/<name>_current`. The `path=` argument is what causes `build-env` to use `conda create -p` instead of `-n`.
  4. Diff old vs. new `conda list --explicit` and `pip freeze` lock files. If identical, **delete the just-made archive** (no point keeping a redundant snapshot).
- **Retention**: archives older than `RETENTION_DAYS` (default 7) are deleted in the cleanup stage.
- **Slack**: posts a per-env diff to `#simsci-shared-env-updates` on success when any env's lock file changed; posts a failure message with a restore hint on failure.
- **Restore from archive**:
  ```bash
  mkdir /tmp/restore && tar xzf <archive>.tar.gz -C /tmp/restore && /tmp/restore/bin/conda-unpack
  ```
- **Permissions**: shared envs are `chmod -R 755` — readable by everyone, writable only by the Jenkins service account.

The pipeline runs builds **sequentially**, not in parallel, to avoid conda package-cache contention (lock conflicts, partial writes, solver resource exhaustion). Don't "optimize" this without understanding why.

---

## `environment.sh` — the convenience wrapper (mncnh only)

`vivarium_gates_mncnh/environment.sh` wraps both `build-env` and `build-shared-env` with auto-detection so contributors don't have to remember the right `make` invocation. **Only `vivarium_gates_mncnh` ships this today.** If the user asks about it in another repo, point them to the mncnh version.

### Usage

**Must be sourced, not executed** — it activates the env in the current shell. The script detects non-sourced invocation and exits with an explicit error message.

```bash
source environment.sh           # build/activate a 'simulation' conda env, only if missing/stale
source environment.sh -t artifact
source environment.sh -s        # shared-env venv overlay (recommended on the cluster)
source environment.sh -f        # force rebuild even if a fresh env exists
source environment.sh -l        # also install git-lfs (conda mode only)
source environment.sh -h        # help
```

### What it does

1. **Pulls latest** for the current branch if it exists on `origin`.
2. Computes `env_name = <repo-dir-basename>_<type>`.
3. If `-s` (shared mode):
   - Deactivates any currently active conda envs (`CONDA_SHLVL` levels).
   - If `.venv/<env_name>` is missing or `-f` given, runs `make build-shared-env type=<type> force=yes`.
   - Sources `.venv/<env_name>/bin/activate`.
4. Else (conda mode):
   - Initializes conda from `$(conda info --base)/etc/profile.d/conda.sh` if not already initialized.
   - Looks for an existing conda env named `<env_name>`. If it exists and `-f` not given, reads its creation timestamp from the first line of `$CONDA_PREFIX/conda-meta/history` and compares against `days_until_stale=7`.
   - **If older than 7 days → rebuilds. If newer → reuses.** This is the "staleness" behavior contributors ask about.
   - If rebuilding, runs `make build-env type=<type> name=<env_name> force=yes [lfs=yes]`.
   - `conda activate <env_name>`.

### Sourcing safety

The script sets `trap '... return' ERR` and `set -E` so that an error inside it returns from the source instead of killing the parent shell, and then clears the trap on the way out so subsequent commands in the calling shell aren't affected. Don't "clean this up" — it's load-bearing.

---

## Surprises and gotchas

- **`build-env` is per-repo, not in VBU's `base.mk`.** It has to exist before VBU is installed. If a repo's `build-env` drifts from the mncnh template, that's a per-repo decision, not a bug.
- **`make install` runs `setup-slack` every time.** Idempotent but noisy.
- **`build-shared-env` will refuse to run if the shared env doesn't exist.** First-time setup on a new model requires adding it to `ACTIVE_MODELS` in the Jenkins job and running the pipeline once.
- **Venv overlay needs the activate-script patch.** If a user reports "psimulate not found" from inside a venv, check that the activate script has the appended `PATH=...` block — an unpatched venv won't find shared-env entry points.
- **`environment.sh` must be sourced.** Executed directly, it exits early with an error. Don't add it to a script's shebang line.
- **Conda env staleness threshold is 7 days**, hard-coded in `environment.sh` (`days_until_stale=7`). Matches the Jenkins shared-env retention default — keep them in sync if either changes.
- **`include_timestamp=yes`** is mainly for archived/named-snapshot use cases. Don't set it for everyday dev — your shell history will fill with `<pkg>_simulation_20260519_141253`.
- **`force=yes` actually does `conda remove --all`.** It's destructive. Warn the user if they're about to lose an env with uncommitted state (compiled extensions, manually-installed packages, etc.).

## When the user asks "what env am I in?"

```bash
echo "CONDA_DEFAULT_ENV=$CONDA_DEFAULT_ENV"
echo "CONDA_PREFIX=$CONDA_PREFIX"
echo "VIRTUAL_ENV=$VIRTUAL_ENV"
which python
python -c "import sys; print(sys.prefix)"
```

A venv overlay shows `VIRTUAL_ENV` set and `CONDA_DEFAULT_ENV` unset (the overlay deactivates conda first). A plain conda env shows the inverse.
