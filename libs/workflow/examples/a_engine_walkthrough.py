# %% [markdown]
# # A-engine walkthrough — run it, don't trust it
#
# This is the validation for the A-engine workflow cut. It is not a report to read:
# it runs the real `vivarium.workflow` code on this branch, so the numbers and
# reasons you see are the ones *you* just produced.
#
# Two ways to use it, from `libs/workflow` with the dev env active:
#   - as a script:   `python examples/a_engine_walkthrough.py`
#   - as a notebook: open it in VS Code or Jupyter (the `# %%` markers are cells) and
#                    run cell by cell, then edit and re-run to play.
#
# What it shows: author a pipeline with `python_step`, see it wired, preview the plan,
# run it, and watch the always-stale default re-run everything. Then it gives the
# steps a stable code identity to show that the engine re-runs only what changed,
# shows how a step is described so another process can rebuild it, and runs a
# command step in its own environment. Places marked "PLAY" are meant to be edited.

# %%
import tempfile
from pathlib import Path

import pandas as pd

from vivarium.workflow.backend.build import build, outcomes
from vivarium.workflow.backend.nodes import AlwaysStaleNode
from vivarium.workflow.backend.preview import preview
from vivarium.workflow.backend.wiring import ArtifactRegistry
from vivarium.workflow.frontend.python_step import python_step

WORK = Path(tempfile.mkdtemp(prefix="a_engine_"))
STATE = WORK / "state"
print(f"workspace: {WORK}")


def show_plan(tasks, state):
    """Dry-run preview: print would-run and the engine's own reasons per step."""
    for step in preview(tasks, state):
        print(f"  {step.name:<14} would_run={step.would_run}")
        for reason in step.reasons:
            print(f"      - {reason}")


def run_build(tasks, state):
    """Build for real: run what is stale and return each step's outcome."""
    return {name: oc.name for name, oc in outcomes(build(tasks, state)).items()}


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
print("outcomes:", run_build(tasks, STATE))
for key in ("raw", "mid.parquet", "final.parquet"):
    path = raw if key == "raw" else reg.path_for(key)
    print(f"  {key:<14} n = {pd.read_parquet(path)['n'].tolist()}")

# %% [markdown]
# ## 5. The always-stale default — preview again with NOTHING changed
#
# This is the honest centerpiece. Nothing was touched on disk, yet every step still
# wants to run — and the reason names `<step>::code` as changed. That is the
# always-stale code identity that every `python_step` step gets. A per-target code
# fingerprint, which would let an unchanged step skip, is deferred to A-caching
# (MIC-7520). Section 7 shows that the engine does skip once a step has a stable
# code identity; `python_step` steps cannot declare one yet.

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
from vivarium.workflow.harness import Perturbed  # noqa: E402

with Perturbed(raw, b"\n# perturbed\n"):
    print("preview while raw is perturbed:")
    show_plan(tasks, STATE)
print("raw restored:", pd.read_parquet(raw)["n"].tolist())

# %% [markdown]
# ## 7. Selective staleness with a stable code identity
#
# `python_step` itself is still always-stale: a step cannot declare a code
# fingerprint until MIC-7520. The test harness has a stand-in, `stable_code_id`,
# whose state changes only when you change its `version` string. Giving each step
# one shows what the engine does once code identity is stable.
#
# The pipeline here is the one from section 1 plus a third step, `side.parquet`, fed
# by a second root input. That step is outside `raw`'s cone (the steps downstream of
# `raw`), so it is the control: it should skip in every plan below.
#
# Look for three things:
#   - a preview with nothing changed skips every step;
#   - perturbing `raw` makes `mid.parquet` and `final.parquet` would-run, and
#     `side.parquet` stays skipped;
#   - editing `double` and bumping its code version re-runs `mid.parquet` and its
#     descendant `final.parquet` only.
#
# A note on the last one. The preview marks every descendant of a changed step as
# would-run, because it cannot know yet whether the new product will differ. The run
# checks again once the producer has run: if the new product is byte-identical, the
# descendant is skipped. PLAY: set `double_edited` back to `* 2` and re-run this cell
# to see `final.parquet` skip at run time.

# %%
from dataclasses import replace  # noqa: E402

from vivarium.workflow.harness import stable_code_id  # noqa: E402

extra = WORK / "extra.parquet"
pd.DataFrame({"n": [5, 6]}).to_parquet(extra)
reg_stable = ArtifactRegistry(WORK / "out", "run2", root_inputs={"raw": raw, "extra": extra})
STATE_STABLE = WORK / "state_stable"


def stable_step(target, inputs, produces, version="1"):
    """Compile a step with a fixed code identity; a new version stands in for a code edit."""
    task = python_step(target, inputs=inputs, produces=produces, registry=reg_stable)
    return replace(task, code_id=stable_code_id(f"{task.name}::code", version))


def triple(extra):
    return pd.read_parquet(extra).assign(n=lambda d: d.n * 3)


stable_tasks = [
    stable_step(double, {"data": "raw"}, "mid.parquet"),
    stable_step(offset, {"mid": "mid.parquet"}, "final.parquet"),
    stable_step(triple, {"extra": "extra"}, "side.parquet"),
]
print("first build:", run_build(stable_tasks, STATE_STABLE))

print("\npreview with nothing changed (expect every step skipped):")
show_plan(stable_tasks, STATE_STABLE)

with Perturbed(raw, b"\n# perturbed\n"):
    print("\npreview while raw is perturbed (expect mid and final to run, side skipped):")
    show_plan(stable_tasks, STATE_STABLE)


def double_edited(data):  # PLAY: set this back to `* 2`
    return pd.read_parquet(data).assign(n=lambda d: d.n * 4)


edited_tasks = [
    stable_step(double_edited, {"data": "raw"}, "mid.parquet", version="2"),
    *stable_tasks[1:],
]
print("\npreview after editing double and bumping its version to 2:")
show_plan(edited_tasks, STATE_STABLE)
print("\nbuild:", run_build(edited_tasks, STATE_STABLE))
print("final.parquet n =", pd.read_parquet(reg_stable.path_for("final.parquet")).n.tolist())

# %% [markdown]
# ## 8. How a step crosses to another process (Stage 0b)
#
# A task's `run` is not a closure. For a `python_step` it is a `PythonStepRun`, a
# small frozen object that holds only the target function and the product suffix.
# Another process can rebuild an equal run from the target's import reference and
# the suffix.
#
# That needs a reference the other process can import. `target_reference` refuses
# a function it cannot name that way, and says why. This walkthrough's own `double`
# is defined in `__main__`, as every function in a script or notebook is, so it is
# refused. A function at the top level of an importable module works, and
# `resolve_reference` returns the very same function.
#
# Local runs accept any function, as sections 1 to 7 show. Only the cluster bridge
# (increment B) will need the reference.

# %%
from vivarium.workflow import harness  # noqa: E402
from vivarium.workflow.frontend.python_step import (  # noqa: E402
    PythonStepRun,
    resolve_reference,
    target_reference,
)

step_run = tasks[0].run
print("run is a PythonStepRun:", isinstance(step_run, PythonStepRun))
print(f"  target = {step_run.target.__name__}, suffix = {step_run.suffix!r}")

try:
    target_reference(double)
except ValueError as error:
    print("target_reference(double) refused:", error)

reference = target_reference(harness._double)
print("target_reference(harness._double):", repr(reference))
print(
    "resolve_reference returns the same function:",
    resolve_reference(reference) is harness._double,
)

# %% [markdown]
# ## 9. Running a command step in its own environment
#
# A command step is a shell command string, and it may declare `env`: a conda env or
# venv, by name or by prefix path. The command runs with that env's `bin` directory
# first on `PATH`.
#
# Here the env is a fake prefix under the workspace. It holds an empty `bin/python`
# (which is what marks a directory as an env prefix) and a small tool script that
# exists nowhere else. The tool writes its own path into its argument, so the
# product says which `bin` directory the tool came from. Look for that path to be
# inside the fake env.
#
# Then a second build declares an env that does not resolve, next to a good step.
# `build` checks every declared env before it runs anything, so it refuses the whole
# build with a `ValueError`, and the good step's product is never written.

# %%
import shutil  # noqa: E402

from vivarium.workflow.backend.nodes import file_node  # noqa: E402
from vivarium.workflow.backend.task import Task  # noqa: E402

TOOL = "a_engine_walkthrough_tool"
FAKE_ENV = WORK / "fake_env"
(FAKE_ENV / "bin").mkdir(parents=True)
(FAKE_ENV / "bin" / "python").touch()
tool = FAKE_ENV / "bin" / TOOL
tool.write_text('#!/bin/sh\necho "written by $0" > "$1"\n')
tool.chmod(0o755)
print(f"{TOOL} on this process's PATH:", shutil.which(TOOL))


def command_step(name, product, env):
    """A command step that runs the env's tool to write `product`."""
    return Task(
        name=name,
        run=f"{TOOL} {product}",
        inputs={},
        outputs={"product": file_node(product)},
        resources={},
        env=env,
    )


STATE_ENV = WORK / "state_env"
env_product = WORK / "env_step.txt"
env_tasks = [command_step("env_step", env_product, str(FAKE_ENV))]
print("outcomes:", run_build(env_tasks, STATE_ENV))
print("product says:", env_product.read_text().strip())

good_product = WORK / "good_step.txt"
mixed_tasks = [
    command_step("good_step", good_product, str(FAKE_ENV)),
    command_step("bad_env_step", WORK / "bad_env_step.txt", str(WORK / "no_such_env")),
]
try:
    build(mixed_tasks, STATE_ENV)
except ValueError as error:
    print("\nbuild refused:", error)
print("good_step's product was written:", good_product.exists())

# %% [markdown]
# ## What this validates, and what it does not
#
# Validated here, by your own run: authoring (`python_step`), wiring (edges from
# shared node identity), preview (the plan and its reasons), and run (real products
# flowing through the chain). With a stable code identity, the engine re-runs exactly
# the cone of a change and skips everything else. A `python_step` run is a data-only
# object that names its target by import reference, and that reference is refused,
# with a plain reason, for a function another process could not import. A command
# step runs with its declared env first on `PATH`, and a build with an env that does
# not resolve is refused before any step runs.
#
# Not here, by design: real code fingerprints (A-caching, MIC-7520). The stable code
# identity in section 7 is the test harness's stand-in, so `python_step` steps still
# re-run on every build. Also not here: cluster execution (the Jobmon bridge,
# increment B), which is what will use the import reference. The harness fixture
# used by the test suite is `vivarium.workflow.harness.build_fixture`, if you want a
# ready-made pipeline to poke at instead of the one above.
