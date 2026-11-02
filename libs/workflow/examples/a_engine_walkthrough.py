# %% [markdown]
# # A-engine walkthrough
#
# This notebook is the evidence for the
# [A-engine validation review](https://claude.ai/artifact/Uqw5FZv9nmqA3q1fdMxLPW). Each
# section is headed by a criterion from the review's "Validation criteria" table, with
# the same ID and in the same order. Run it top to bottom: every number and reason
# below comes from your own run of the `vivarium.workflow` code on this branch.
#
# A6 and A7 are about the test suite, not the engine, so the review doc shows them.
# Cells for exploring on your own are at the end, under PLAY, with three previews of
# behavior this increment supports early and a later increment signs off.
#
# Run it from `libs/workflow` with the dev env active, as a notebook or with
# `python examples/a_engine_walkthrough.py`.

# %%
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import pandas as pd

from vivarium.workflow import harness
from vivarium.workflow.backend.build import build, outcomes
from vivarium.workflow.backend.nodes import AlwaysStaleNode, file_node
from vivarium.workflow.backend.preview import preview
from vivarium.workflow.backend.task import Task
from vivarium.workflow.backend.wiring import ArtifactRegistry, WiringError
from vivarium.workflow.frontend.python_step import python_step, target_reference
from vivarium.workflow.harness import Perturbed, stable_code_id

WORK = Path(tempfile.mkdtemp(prefix="a_engine_"))
print(f"workspace: {WORK}")


def show_plan(tasks, state):
    """Preview without running: print whether each step would run, and pytask's reasons."""
    for step in preview(tasks, state):
        print(f"  {step.name:<14} would_run={step.would_run}")
        for reason in step.reasons:
            print(f"      - {short_reason(reason)}")


def short_reason(reason):
    """Shorten pytask's change reason to 'node: reason'."""
    match = re.search(r"node_name='([^']*)'.*?reason='([^']*)'", reason)
    if match is None:
        return reason
    node, why = match.groups()
    return f"{Path(node).name if '/' in node else node}: {why}"


def run_build(tasks, state):
    """Build for real: run what is stale and return each step's outcome."""
    return {name: outcome.name for name, outcome in outcomes(build(tasks, state)).items()}


def values(path):
    """Return the `n` column of a parquet product."""
    return pd.read_parquet(path)["n"].tolist()


def command_task(name, command, inputs=None, outputs=None, env=None):
    """A command step: a shell command with file nodes in and out."""
    return Task(
        name=name,
        run=command,
        inputs=inputs or {},
        outputs=outputs or {},
        resources={},
        env=env,
    )


# %% [markdown]
# ## A1 — A pipeline is declared once, and each consumer's input resolves to the path its producer writes
#
# Two steps: `double` reads the root input `raw`, and `offset` reads the key
# `mid.parquet`, which `double` produces. Nothing declares the edge between them. Look
# for the consumer's input path to equal the producer's output path: pytask connects
# steps whose nodes share a path. A4 shows the data crossing that edge.

# %%
raw = WORK / "raw.parquet"
pd.DataFrame({"n": [1, 2, 3]}).to_parquet(raw)
reg = ArtifactRegistry(WORK / "out", "run1", root_inputs={"raw": raw})
STATE = WORK / "state"


def double(data):
    return pd.read_parquet(data).assign(n=lambda d: d.n * 2)


def offset(mid):
    return pd.read_parquet(mid).assign(n=lambda d: d.n + 10)


tasks = [
    python_step(double, inputs={"data": "raw"}, produces="mid.parquet", registry=reg),
    python_step(
        offset, inputs={"mid": "mid.parquet"}, produces="final.parquet", registry=reg
    ),
]
producer, consumer = tasks
produced = producer.outputs["product"].path
final = reg.path_for("final.parquet")
print("steps:          ", [task.name for task in tasks])
print("producer writes:", produced)
print("consumer reads: ", consumer.inputs["mid"].path)
print("same path:      ", consumer.inputs["mid"].path == produced)

# %% [markdown]
# ## A2 — Wiring mistakes fail when the pipeline is defined, with a message naming the key
#
# Three mistakes, each on a throwaway registry so the pipeline above is untouched. Each
# raises a `WiringError` while the steps are being declared, before anything runs.

# %%
def expect_wiring_error(label, action):
    """Run `action` and print the WiringError it raises."""
    try:
        action()
    except WiringError as error:
        print(f"{label}:\n    {error}")
    else:
        print(f"{label}: no error, which is wrong")


typo = ArtifactRegistry(WORK / "out", "typo", root_inputs={"raw": raw})
python_step(double, inputs={"data": "raw"}, produces="mid.parquet", registry=typo)
expect_wiring_error(
    "an unknown input key",
    lambda: python_step(
        offset, inputs={"mid": "mid.parqet"}, produces="final.parquet", registry=typo
    ),
)

two_keys = ArtifactRegistry(WORK / "out", "two_keys")
two_keys.register_output("sub/x.csv")
expect_wiring_error(
    "two keys on one path", lambda: two_keys.register_output("sub/../sub/x.csv")
)

collide = ArtifactRegistry(
    WORK / "out", "collide", root_inputs={"raw": WORK / "out" / "collide" / "x.csv"}
)
expect_wiring_error(
    "an output on a root input's path", lambda: collide.register_output("x.csv")
)

# %% [markdown]
# ## A3 — Preview shows, per step, whether it would run and why, and it runs nothing and writes no product
#
# Nothing has been built, so both steps would run: the first because its code, its
# input and its product are new, the second as a cascade from the first. Afterwards,
# neither product exists.

# %%
show_plan(tasks, STATE)
print("products exist after the preview:", produced.exists(), final.exists())

# %% [markdown]
# ## A4 — A build runs the pipeline end to end and reports each step's outcome; a failed step blocks its descendants
#
# The build runs both steps, and the data crosses A1's edge: `[1, 2, 3]` becomes
# `[2, 4, 6]` and then `[12, 14, 16]`.
#
# Then a separate pipeline whose first step exits with an error. pytask prints its
# failure report; look for the outcomes after it: the step fails, its two descendants
# are skipped, and the last product is never written.

# %%
print("outcomes:", run_build(tasks, STATE))
print("raw:          ", values(raw))
print("mid.parquet:  ", values(produced))
print("final.parquet:", values(final))

failing = WORK / "failing"
failing.mkdir()
out, child, grandchild = failing / "out", failing / "child", failing / "grandchild"
failing_tasks = [
    command_task("fail", "exit 3", outputs={"out": file_node(out)}),
    command_task(
        "child",
        f"cp {out} {child}",
        inputs={"in": file_node(out)},
        outputs={"out": file_node(child)},
    ),
    command_task(
        "grandchild",
        f"cp {child} {grandchild}",
        inputs={"in": file_node(child)},
        outputs={"out": file_node(grandchild)},
    ),
]
failing_outcomes = run_build(failing_tasks, failing / "state")
print("outcomes:", failing_outcomes)
print("grandchild's product was written:", grandchild.exists())

# %% [markdown]
# ## A5 — A step with no declared code fingerprint re-runs on every build
#
# Nothing changed on disk since A4, yet both steps would run again. The first step's
# reason is its code node, `mid.parquet::code`, which every `python_step` gets as an
# always-stale default until A-caching adds fingerprints (MIC-7760). The second runs
# as a cascade.

# %%
print(
    "code identity is the always-stale default:",
    isinstance(consumer.code_id, AlwaysStaleNode),
)
show_plan(tasks, STATE)

# %% [markdown]
# ## A6 and A7 — the test suite
#
# These two criteria are about the test suite, not the engine, so the review doc shows
# them. A6 is the CI and `make check` results and the coverage table. A7 is the two
# break drills: a one-line edit to `ArtifactRegistry.resolve_input` that the suite
# catches, with 9 failures for the loud drill and 3 for the quiet one. The doc's
# "Reproduce it yourself" section has the commands.

# %% [markdown]
# ## PLAY
#
# Nothing below is part of the review. Edit and re-run to explore: for example, change
# `double` or `offset` in A1, re-run A1 to A5, and watch the plans and values.
#
# The three previews below show behavior the backend and its tests already support,
# which a later increment signs off: content-based skips (A-caching), a command step in
# its own env (B), and a Python step sent to another process (B).

# %% [markdown]
# ### Early support for A-caching: file nodes compare by content, so with a stable code id only the changed input's cone re-runs
#
# A-caching signs this off with real code fingerprints (WF-11, WF-13). Here
# `stable_code_id`, a test-harness stand-in for a fingerprint, keeps a step's code
# identity fixed until its version changes. The pipeline is A1's plus a third step,
# `side.parquet`, fed by a second root input, so it is outside `raw`'s cone.
#
# Look for three plans. With nothing changed, every step skips. With `raw` rewritten
# with the same bytes (a new modification time, the same content), every step still
# skips. With `raw` given new content, its two downstream steps would run and
# `side.parquet` stays skipped. Give `raw` your own bytes in the `Perturbed` block to
# explore.

# %%
extra = WORK / "extra.parquet"
pd.DataFrame({"n": [5, 6]}).to_parquet(extra)
reg_stable = ArtifactRegistry(
    WORK / "out", "stable", root_inputs={"raw": raw, "extra": extra}
)
STATE_STABLE = WORK / "state_stable"


def stable_step(target, inputs, produces, version="1"):
    """Compile a step and give it a fixed code identity."""
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

print("\nnothing changed:")
show_plan(stable_tasks, STATE_STABLE)

raw.write_bytes(raw.read_bytes())
print("\nraw rewritten with the same bytes:")
show_plan(stable_tasks, STATE_STABLE)

with Perturbed(raw, b"\n# perturbed\n"):
    print("\nraw given new content:")
    show_plan(stable_tasks, STATE_STABLE)

# %% [markdown]
# The cell below edits `double` and bumps its code version, which stands in for a code
# change once fingerprints exist: `mid.parquet` and `final.parquet` re-run and
# `side.parquet` stays skipped. Set `double_edited` back to `* 2` and run it again:
# `final.parquet` then skips at run time, because its new input is byte-identical.

# %%
def double_edited(data):  # PLAY: set this back to `* 2`
    return pd.read_parquet(data).assign(n=lambda d: d.n * 4)


edited_tasks = [
    stable_step(double_edited, {"data": "raw"}, "mid.parquet", version="2"),
    *stable_tasks[1:],
]
print("preview after editing double and bumping its version to 2:")
show_plan(edited_tasks, STATE_STABLE)
print("\nbuild:", run_build(edited_tasks, STATE_STABLE))
print("final.parquet:", values(reg_stable.path_for("final.parquet")))


# %% [markdown]
# ### Early support for B: a command step runs with its declared env's `bin` directories first on `PATH`, and a build whose env does not resolve is refused before any step runs
#
# B signs this off, with steps running in their declared envs on cluster nodes (WF-8).
#
# The env is a fake prefix in the workspace: an empty `bin/python` marks it as an env,
# and its `bin/` holds a tool that exists nowhere else. The tool writes its own path
# into its product, so the product says which `bin` it came from. Then a build that
# pairs a good step with a step whose env does not exist is refused before either
# runs.

# %%
TOOL = "a_engine_walkthrough_tool"
FAKE_ENV = WORK / "fake_env"
(FAKE_ENV / "bin").mkdir(parents=True)
(FAKE_ENV / "bin" / "python").touch()
tool = FAKE_ENV / "bin" / TOOL
tool.write_text('#!/bin/sh\necho "written by $0" > "$1"\n')
tool.chmod(0o755)
print(f"{TOOL} on this process's PATH:", shutil.which(TOOL))


def tool_step(name, product, env):
    """A command step that runs the env's tool to write `product`."""
    return command_task(
        name, f"{TOOL} {product}", outputs={"product": file_node(product)}, env=env
    )


STATE_ENV = WORK / "state_env"
env_product = WORK / "env_step.txt"
print("outcomes:", run_build([tool_step("env_step", env_product, str(FAKE_ENV))], STATE_ENV))
print("product says:", env_product.read_text().strip())

good_product = WORK / "good_step.txt"
mixed = [
    tool_step("good_step", good_product, str(FAKE_ENV)),
    tool_step("bad_env_step", WORK / "bad_env_step.txt", str(WORK / "no_such_env")),
]
try:
    build(mixed, STATE_ENV)
except ValueError as error:
    print("\nbuild refused:", error)
print("good_step's product was written:", good_product.exists())

# %% [markdown]
# ### Early support for B: a Python step can be sent to another process and rebuilt there
#
# B runs each step on a compute node, in a different process from the one that built
# the pipeline. A function object cannot be sent there, so a step is sent as data: its
# function's import path and its product suffix. A `python_step`'s `run` is a
# `PythonStepRun` that holds only those two things, and `target_reference` gives the
# import path (`package.module:function`).
#
# This section rebuilds each step of a small pipeline in a fresh Python process and
# checks that it writes the same products. It also shows the refusal you get for a
# function defined here in the notebook, which another process cannot import.
#
# B signs this off, with its real codec sending steps to compute nodes.

# %%
print("run:", tasks[0].run)
try:
    target_reference(double)
except ValueError as error:
    print("\ntarget_reference(double) refused:", error)

crossing = WORK / "crossing"
crossing.mkdir()
fixture = harness.build_fixture(crossing)
print("\nlocal build:", run_build(fixture.tasks, fixture.state))

REBUILD = """
import json
import sys
from pathlib import Path

from vivarium.workflow.frontend.python_step import PythonStepRun, resolve_reference

for reference, suffix, inputs, product in json.loads(sys.argv[1]):
    step_run = PythonStepRun(resolve_reference(reference), suffix)
    step_run(product=Path(product), **{name: Path(path) for name, path in inputs.items()})
"""
jobs, pairs = [], []
for task in fixture.tasks:
    [local] = [node.path for node in task.outputs.values()]
    remote = crossing / "remote" / local.name
    inputs = {name: str(node.path) for name, node in task.inputs.items()}
    jobs.append((target_reference(task.run.target), task.run.suffix, inputs, str(remote)))
    pairs.append((local, remote))
    print(f"  {task.name} is sent as {jobs[-1][0]!r}")
subprocess.run([sys.executable, "-I", "-c", REBUILD, json.dumps(jobs)], check=True)
for local, remote in pairs:
    same = pd.read_parquet(local).equals(pd.read_parquet(remote))
    print(f"  {local.name}: the fresh process wrote the same product: {same}")
