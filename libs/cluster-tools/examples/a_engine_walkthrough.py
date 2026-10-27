# %% [markdown]
# # A-engine walkthrough — run it, don't trust it
#
# This is the validation for the A-engine workflow cut. It is not a report to read:
# it runs the real `vivarium.cluster_tools` code on this branch, so the numbers and
# reasons you see are the ones *you* just produced.
#
# Two ways to use it, from `libs/cluster-tools` with the dev env active:
#   - as a script:   `python examples/a_engine_walkthrough.py`
#   - as a notebook: open it in VS Code or Jupyter (the `# %%` markers are cells) and
#                    run cell by cell, then edit and re-run to play.
#
# What it shows: author a pipeline with `python_step`, see it wired, preview the plan,
# run it, and watch the always-stale default re-run everything. Places marked
# "PLAY" are meant to be edited.

# %%
import tempfile
from pathlib import Path

import pandas as pd

from vivarium.cluster_tools.core.backend.build import build, outcomes
from vivarium.cluster_tools.core.backend.nodes import AlwaysStaleNode
from vivarium.cluster_tools.core.backend.preview import preview
from vivarium.cluster_tools.core.backend.wiring import ArtifactRegistry
from vivarium.cluster_tools.core.frontend.python_step import python_step

WORK = Path(tempfile.mkdtemp(prefix="a_engine_"))
STATE = WORK / "state"
print(f"workspace: {WORK}")


def show_plan(tasks, state):
    """Dry-run preview: print would-run and the engine's own reasons per step."""
    for step in preview(tasks, state):
        print(f"  {step.name:<14} would_run={step.would_run}")
        for reason in step.reasons:
            print(f"      - {reason}")


# %% [markdown]
# ## 1. Author a pipeline
#
# A step is a plain function that reads its inputs (as file paths) and returns a
# DataFrame. `python_step` compiles each into one backend task and wires it through
# the registry. PLAY: change a transform, or add a third step, then re-run everything.

# %%
raw = WORK / "raw.parquet"
pd.DataFrame({"n": [1, 2, 3]}).to_parquet(raw)

reg = ArtifactRegistry(WORK / "out", "run1", root_inputs={"raw": raw})


def double(data):  # PLAY: change `* 2`
    return pd.read_parquet(data).assign(n=lambda d: d.n * 2)


def offset(mid):  # PLAY: change `+ 10`
    return pd.read_parquet(mid).assign(n=lambda d: d.n + 10)


tasks = [
    python_step(double, inputs={"data": "raw"}, produces="mid.parquet", registry=reg),
    python_step(offset, inputs={"mid": "mid.parquet"}, produces="final.parquet", registry=reg),
]
print("steps:", [t.name for t in tasks])

# %% [markdown]
# ## 2. See the wiring — introspect the Task objects
#
# The edge is not declared anywhere; it exists because the consumer's input node is
# the *same object* the producer registered. Confirm it yourself:

# %%
producer, consumer = tasks[0], tasks[1]
shared = reg.resolve_input("mid.parquet")
print("producer's product node is the consumer's input node:",
      list(producer.outputs.values())[0] is shared is list(consumer.inputs.values())[0])
print("consumer code identity is the always-stale default:",
      isinstance(consumer.code_id, AlwaysStaleNode))

# %% [markdown]
# ## 3. See the plan before paying (first build)
#
# Nothing is recorded yet, so every step would run.

# %%
print("first build:")
show_plan(tasks, STATE)

# %% [markdown]
# ## 4. Run it — real products
#
# Running executes each step and autosaves its product. The data flows through the
# shared edge; read the products back yourself.

# %%
print("outcomes:", {name: oc.name for name, oc in outcomes(build(tasks, STATE)).items()})
for key in ("raw", "mid.parquet", "final.parquet"):
    path = raw if key == "raw" else reg.path_for(key)
    print(f"  {key:<14} n = {pd.read_parquet(path)['n'].tolist()}")

# %% [markdown]
# ## 5. The always-stale default — preview again with NOTHING changed
#
# This is the honest centerpiece. Nothing was touched on disk, yet every step still
# wants to run — and the reason names `<step>::code` as changed. That is the
# always-stale code identity; a per-target fingerprint (which would let unchanged
# steps skip) is deferred to A-caching. Selective staleness is NOT claimed here.

# %%
print("converged preview (nothing changed on disk):")
show_plan(tasks, STATE)

# %% [markdown]
# ## 6. PLAY: perturb an input and preview
#
# Changing `raw` changes its content hash, so the preview flags the raw input too
# (on top of the always-stale code). Everything still runs. Edit the bytes, or the
# transforms above, and re-run to see the reasons change.

# %%
from vivarium.cluster_tools.core.harness import Perturbed  # noqa: E402

with Perturbed(raw, b"\n# perturbed\n"):
    print("preview while raw is perturbed:")
    show_plan(tasks, STATE)
print("raw restored:", pd.read_parquet(raw)["n"].tolist())

# %% [markdown]
# ## What this validates, and what it does not
#
# Validated here, by your own run: authoring (`python_step`), wiring (edges from
# shared node identity), preview (the plan and its reasons), and run (real products
# flowing through the chain).
#
# Not here, by design: selective staleness (deferred with the code fingerprint,
# A-caching — so everything re-runs), and cluster execution (the Jobmon bridge,
# increment B). The harness fixture used by the test suite is
# `vivarium.cluster_tools.core.harness.build_fixture`, if you want a ready-made
# pipeline to poke at instead of the one above.
