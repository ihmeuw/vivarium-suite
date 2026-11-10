#!/usr/bin/env bash
# Build the two envs the walkthrough's C1-real section needs, as venvs over MNCNH's shared
# artifact env, inside an MNCNH checkout. Run it on a compute node (the login node stops
# long Python processes), with uv on PATH.
#
# Usage: build_mncnh_envs.sh <MNCNH checkout> <libs/dagger of this branch>
#
#   .venv/vivarium_gates_mncnh_artifact  the env the notebook step declares
#   .venv/dagger_mncnh                   the pipeline env: MNCNH plus this branch's dagger,
#                                        with jupytext and nbconvert to run the walkthrough
set -euo pipefail
MNCNH=$(cd "$1" && pwd)
DAGGER=$(cd "$2" && pwd)
SHARED=${MNCNH_SHARED_ENV:-/mnt/team/simulation_science/priv/engineering/jenkins/shared_envs/vivarium_gates_mncnh_artifact_current}

cd "$MNCNH"
for name in vivarium_gates_mncnh_artifact dagger_mncnh; do
  rm -rf ".venv/$name"
  uv venv -q --system-site-packages --python "$SHARED/bin/python" ".venv/$name"
  uv pip install -q --python ".venv/$name/bin/python" -e . --no-deps
done
# Resolve from the MNCNH directory, so its [tool.uv] overrides (pandas<3, numpy<2) apply.
uv pip install -q --python .venv/dagger_mncnh/bin/python -e "$DAGGER" jupytext nbconvert
.venv/dagger_mncnh/bin/python -c "
import importlib.metadata as m, vivarium.dagger
for d in ('vivarium-dagger', 'vivarium_gates_mncnh', 'pandas', 'numpy', 'pytask', 'papermill'):
    print(f'{d:22} {m.version(d)}')
print('dagger from', vivarium.dagger.__file__)"
