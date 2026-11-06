# %% [markdown]
# # Track B walkthrough: the Jobmon bridge
#
# This notebook is the evidence for the track B (Jobmon bridge) validation review. Each
# section is headed by a criterion from the review's "Validation criteria" table, with
# the same ID and in the same order. Run it top to bottom: every outcome, host name and
# timing below comes from your own run of the `vivarium.dagger` code on this branch.
#
# Sections marked "fake" build through the harness's `FakeJobmon`, which runs each step
# with the real node runner in a child process, so they run anywhere. Sections marked
# "cluster" build through real Jobmon on Slurm and skip themselves outside a Slurm
# allocation. B10 and B11 are about the test suite, so the review doc shows them.
#
# Run it from `libs/dagger` with an env that has the `cluster` extra and Jupyter, as a
# notebook or with `python examples/b_bridge_walkthrough.py`. On the cluster, start it in
# an allocation on a compute node (`srun -A proj_simscience -p i.q -c 2 --mem 8G -t 3:00:00
# --pty bash`), set `BRIDGE_WALKTHROUGH_DIR` to a new directory on the shared filesystem,
# because steps write their products from other nodes, and set `BRIDGE_WALKTHROUGH_ENV` to
# a conda env that exists at the same path on every node (B5).

# %%
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

import pandas as pd
from pytask import console

from vivarium.dagger.backend.build import build
from vivarium.dagger.backend.lock import RUN_LOCK_NAME, RUN_REFUSED_KEY
from vivarium.dagger.backend.nodes import file_node
from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import ArtifactRegistry
from vivarium.dagger.bridge.state import RUN_STATE_KEY, RUNS_DIR, STATE_FILE
from vivarium.dagger.frontend.python_step import python_step
from vivarium.dagger.harness import FakeJobmon

# Run from libs/dagger or from examples/ (a notebook runs in its own directory).
for EXAMPLES in (Path("examples").resolve(), Path.cwd()):
    if (EXAMPLES / "bridge_walkthrough_lib.py").exists():
        break
else:
    EXAMPLES = Path(__file__).resolve().parent
# A node imports a Python step's target from its own sys.path, so the steps module must
# be importable there. PYTHONPATH carries this directory to every runner process.
os.environ["PYTHONPATH"] = os.pathsep.join(
    p for p in (str(EXAMPLES), os.environ.get("PYTHONPATH", "")) if p
)
sys.path.insert(0, str(EXAMPLES))
import bridge_walkthrough_lib as lib  # noqa: E402

WORK = Path(os.environ.get("BRIDGE_WALKTHROUGH_DIR") or tempfile.mkdtemp(prefix="bridge_"))
WORK.mkdir(parents=True, exist_ok=True)
ON_SLURM = "SLURM_JOB_ID" in os.environ and shutil.which("sbatch") is not None
SLURM = {"jobmon_cluster": "slurm", "jobmon_resources": lib.SLURM_RESOURCES}
CONTROLLER = os.uname().nodename
print(f"workspace: {WORK}")
print(f"controller: {CONTROLLER}, pid {os.getpid()}, python {sys.executable}")
print(f"cluster sections: {'run' if ON_SLURM else 'skipped (not in a Slurm allocation)'}")


def bound(fake):
    """Return the step names of each plan that `fake` bound."""
    return [[entry.name for entry in plan] for plan in fake.plans]


def state_files(root):
    """Return the run state files under a build's root, oldest first."""
    return sorted((root / RUNS_DIR).glob(f"*/{STATE_FILE}"), key=lambda p: p.stat().st_mtime)


def lib_command(*args):
    """Return a command that runs the walkthrough module's main with `args` in a new process.

    The module is imported rather than run as a script, so its step functions keep an
    import path that a node can resolve.
    """
    code = (
        f"import sys; sys.path.insert(0, {str(EXAMPLES)!r}); "
        "import bridge_walkthrough_lib as lib; lib.main(sys.argv[1:])"
    )
    return [sys.executable, "-c", code, *map(str, args)]


def skipped(reason="not in a Slurm allocation"):
    """Say that a cluster section did not run."""
    print(f"skipped: {reason}")


# %% [markdown]
# ## B1 — A failed step blocks its descendants, and their bodies never run (fake)
#
# b raises on its node. a and d succeed, b is FAIL with the node's exception, and c, which
# depends on b, is SKIP_PREVIOUS_FAILED. c has no product, so its body ran nowhere, and
# each step that ran recorded a process id that is not this notebook's.

# %%
base = WORK / "b1_b3"
tasks, registry = lib.chain(base, switch="fail")
with FakeJobmon(workflow_id=101) as fake:
    session = build(tasks, base / "state", jobmon=True)
lib.show(session)
print("\nwhere each step ran:", lib.ran_where(registry, ["a", "b", "c", "d"]))
print(f"this notebook's pid: {os.getpid()}")

# %% [markdown]
# ## B2 — After the cause is fixed, the next build re-runs exactly the failed cone (fake)
#
# The switch now says `ok`. a and d, which succeeded, are SKIP_UNCHANGED, and the one
# plan bound holds exactly b and c.

# %%
(base / "switch.txt").write_text("ok\n")
with FakeJobmon(workflow_id=102) as fake:
    session = build(tasks, base / "state", jobmon=True)
lib.show(session)
print("plans bound:", bound(fake))

# %% [markdown]
# ## B3 — A second build with nothing changed runs nothing and binds no workflow (fake)
#
# Every step is SKIP_UNCHANGED and no plan is bound. The bridged builds wrote the same
# lockfile entries a local build writes, so a local build afterwards also skips every
# step.

# %%
with FakeJobmon(workflow_id=103) as fake:
    session = build(tasks, base / "state", jobmon=True)
lib.show(session)
print("plans bound:", bound(fake))
print("a local build afterwards:", lib.outcome_names(build(tasks, base / "state")))

# %% [markdown]
# ## B4 — On Slurm, a pipeline runs end to end through Jobmon, and its record is read over NFS (cluster)
#
# The same chain, through real Jobmon on Slurm. b fails first, so this also shows B1
# for real: c is SKIP_PREVIOUS_FAILED. After the fix, only b and c run again (B2), each
# on a compute node that is not the controller. Then a build with nothing changed binds
# no workflow (B3), and a build from another node, in its own allocation, reads the same
# record over NFS and also skips every step.

# %%
if ON_SLURM:
    base = WORK / "b4"
    tasks, registry = lib.chain(base, switch="fail")
    session = build(tasks, base / "state", jobmon=True, **SLURM)
    lib.show(session)
    state = session.config[RUN_STATE_KEY]
    print(f"\nJobmon workflow {state.workflow_id}, status {state.workflow_status}")
    print("where each step ran:", lib.ran_where(registry, ["a", "b", "c", "d"]))
else:
    skipped()

# %%
if ON_SLURM:
    (base / "switch.txt").write_text("ok\n")
    started = time.time()
    session = build(tasks, base / "state", jobmon=True, **SLURM)
    b4_wall = time.time() - started
    lib.show(session)
    state = session.config[RUN_STATE_KEY]
    print(f"\nJobmon workflow {state.workflow_id}, status {state.workflow_status}")
    print("where each step ran:", lib.ran_where(registry, ["a", "b", "c", "d"]))
    print(f"controller: {CONTROLLER}; wall time of this build: {b4_wall:.0f} s")
    before = len(state_files(base / "state"))
    again = build(tasks, base / "state", jobmon=True, **SLURM)
    print("\nnothing changed:", lib.outcome_names(again))
    print("new run directories:", len(state_files(base / "state")) - before)
else:
    skipped()

# %%
if ON_SLURM:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("SLURM_")}
    other = subprocess.run(
        [
            "srun",
            "-A",
            "proj_simscience",
            "-p",
            "all.q",
            "-c",
            "1",
            "--mem",
            "4G",
            "-t",
            "00:20:00",
            "--exclude",
            CONTROLLER,
            *lib_command("rebuild", base),
        ],
        env=clean,
        capture_output=True,
        text=True,
        timeout=3600,
    )
    # pytask's own output from that build is left out; the first line names the node,
    # and the last gives each step's outcome and whether a workflow was bound.
    lines = other.stdout.strip().splitlines()
    print(lines[0], lines[-1], sep="\n")
    if other.returncode:
        print(other.stderr[-2000:])
else:
    skipped()

# %% [markdown]
# ## B5 — Each step runs in its declared env, and a Python step is sent by its import path (cluster)
#
# Three steps on compute nodes: a command step that declares the env in
# `BRIDGE_WALKTHROUGH_ENV`, a command step with no env, and a Python step. Each command
# step writes the `python` that its shell finds first; the Python step records its own
# interpreter. A second Python step, rebuilt on a node from its import path, writes the
# same product as the same step built locally. Then two refusals: an env that does not resolve on the controller stops
# the build before anything is submitted, and an env that exists only on the controller's
# local disk fails on the node.

# %%
def which_python(name, out, env=None):
    """A command step that writes the path of the first `python` on its PATH."""
    command = f"command -v python > {out}"
    return lib.stable(
        Task(
            name=name,
            run=command,
            inputs={},
            outputs={"out": file_node(out)},
            resources={},
            env=env,
        )
    )


ENV_X = os.environ.get("BRIDGE_WALKTHROUGH_ENV")
if ON_SLURM and ENV_X:
    base = WORK / "b5"
    out = base / "out"
    out.mkdir(parents=True, exist_ok=True)
    registry = ArtifactRegistry(base / "out", "run1", root_inputs={"seed": base / "seed.txt"})
    (base / "seed.txt").write_text("seed\n")
    tasks = [
        which_python("in_env_x", out / "in_env_x.txt", env=ENV_X),
        which_python("no_env", out / "no_env.txt"),
        lib.stable(
            python_step(
                lib.start,
                inputs={"seed": "seed"},
                produces="py.parquet",
                registry=registry,
                name="py",
            )
        ),
    ]
    session = build(tasks, base / "state", jobmon=True, **SLURM)
    lib.show(session)
    print(f"\nenv X: {ENV_X}")
    print("in_env_x ran:", (out / "in_env_x.txt").read_text().strip())
    print("no_env ran:  ", (out / "no_env.txt").read_text().strip())
    row = pd.read_parquet(registry.path_for("py.parquet")).iloc[-1]
    print(f"py ran:       {row['python']} on {row['host']}")
    print(f"controller:   {sys.executable} on {CONTROLLER}")
else:
    skipped("not in a Slurm allocation, or BRIDGE_WALKTHROUGH_ENV is not set")

# %%
if ON_SLURM:
    on_node, node_registry = lib.doubling(WORK / "b5_same" / "node")
    on_here, here_registry = lib.doubling(WORK / "b5_same" / "local")
    lib.show(build(on_node, WORK / "b5_same" / "node" / "state", jobmon=True, **SLURM))
    build(on_here, WORK / "b5_same" / "local" / "state")
    node_frame = pd.read_parquet(node_registry.path_for("doubled.parquet"))
    here_frame = pd.read_parquet(here_registry.path_for("doubled.parquet"))
    print("on a node:", node_frame["n"].tolist(), "| here:", here_frame["n"].tolist())
    print("same product:", node_frame.equals(here_frame))
else:
    skipped()

# %%
if ON_SLURM:
    missing = WORK / "b5" / "no_such_env"
    try:
        build(
            [which_python("missing_env", WORK / "b5" / "out" / "m.txt", env=str(missing))],
            WORK / "b5" / "state_missing",
            jobmon=True,
            **SLURM,
        )
    except ValueError as error:
        print("refused before submission:", error)
    print("run directories:", len(state_files(WORK / "b5" / "state_missing")))
    local_env = Path(tempfile.mkdtemp(prefix="local_env_", dir="/tmp"))
    (local_env / "bin").mkdir()
    (local_env / "bin" / "python").symlink_to(sys.executable)
    session = build(
        [which_python("local_only_env", WORK / "b5" / "out" / "l.txt", env=str(local_env))],
        WORK / "b5" / "state_local",
        jobmon=True,
        **SLURM,
    )
    lib.show(session)
    print(
        "its product:",
        (WORK / "b5" / "out" / "l.txt").read_text().strip()
        if (WORK / "b5" / "out" / "l.txt").exists()
        else "none",
    )
else:
    skipped()

# %% [markdown]
# ## B6 — An interrupted build settles what it can, keeps the workflow id, and the next build resumes
#
# Fake: the fake stops the run with SIGTERM after two steps. The build records the two
# results, reports every other step, writes an "interrupted" state with the workflow id,
# and returns. The next build runs exactly the steps that were not recorded.

# %%
base = WORK / "b6_fake"
tasks, registry = lib.chain(base)
with FakeJobmon(workflow_id=601, interrupt_after=2) as fake:
    session = build(tasks, base / "state", jobmon=True)
lib.show(session)
state = session.config[RUN_STATE_KEY]
print(f"\nstate: {state.status}, workflow {state.workflow_id}, pending {state.pending}")
with FakeJobmon(workflow_id=602) as fake:
    session = build(tasks, base / "state", jobmon=True)
lib.show(session)
print("plans bound:", bound(fake))

# %% [markdown]
# Cluster: the controller runs as its own Slurm job, building a -> b -> c where b and c
# each take two minutes. Once a has finished, `scancel` cancels the controller's job, so
# Slurm sends SIGTERM and, after its `KillWait` grace period, SIGKILL.
#
# First the controller is the batch script's own process (`exec`), so it receives the
# SIGTERM itself: it records a, settles b and c, writes an "interrupted" state with the
# workflow id, and exits inside the grace period. The next build runs only b and c.
#
# The bridge does not cancel the workflow, so b's job, already running on its node,
# keeps running after the controller is gone and writes b's product, which no build
# records. This is why the next build warns about an unfinished run.
#
# Then the same thing without `exec`. The batch script's shell receives the SIGTERM and
# exits, the job step ends, and Slurm kills the build before its handler runs: nothing is
# recorded and the state stays "running". The next build warns that the earlier run did
# not finish and runs every step again. So a batch script must `exec` the build, or start
# it with `srun`, for the SIGTERM handling to take effect.


# %%
def cancel_a_controller(base, use_exec):
    """Start a controller job building `slow_chain(base)`, scancel it once a is done, and report."""
    lib.slow_chain(base, pause=120)
    log = base / "controller.log"
    command = shlex.join(lib_command("controller", base, "slurm", "120"))
    clean = {k: v for k, v in os.environ.items() if not k.startswith("SLURM_")}
    submitted = subprocess.run(
        [
            "sbatch",
            "--parsable",
            "-A",
            "proj_simscience",
            "-p",
            "all.q",
            "-c",
            "1",
            "--mem",
            "4G",
            "-t",
            "01:00:00",
            "-o",
            str(log),
            "--wrap",
            f"exec {command}" if use_exec else command,
        ],
        env=clean,
        capture_output=True,
        text=True,
        check=True,
    )
    job = submitted.stdout.strip().split(";")[0]
    print(f"controller job {job} ({'exec' if use_exec else 'no exec'})")
    # Cancel once Jobmon has b's job running on a node, as a time limit might.
    deadline = time.time() + 3600
    b_job = ""
    while not b_job and time.time() < deadline:
        time.sleep(5)
        b_job = subprocess.run(
            [
                "squeue",
                "-h",
                "-u",
                os.environ["USER"],
                "-n",
                "b-1",
                "-t",
                "RUNNING",
                "-o",
                "%i",
            ],
            capture_output=True,
            text=True,
        ).stdout.strip()
    print(f"b's job {b_job} is running on a node")
    time.sleep(10)
    cancelled = time.time()
    subprocess.run(["scancel", job], check=True)
    while subprocess.run(
        ["squeue", "-h", "-j", job], capture_output=True, text=True
    ).stdout.strip():
        time.sleep(2)
    print(f"job gone {time.time() - cancelled:.0f} s after scancel")
    finished = [
        json.loads(line) for line in log.read_text().splitlines() if line.startswith("{")
    ]
    if finished:
        print(f"the build returned {finished[-1]['time'] - cancelled:.1f} s after scancel")
    else:
        print("the build printed no result: it was killed before it returned")
    tail = [
        line
        for line in log.read_text().splitlines()
        if line.startswith(("{", "[")) or "CANCELLED" in line
    ]
    print("controller log:", *tail, sep="\n  ")
    [path] = state_files(base / "state")[-1:]
    print("state file:", json.loads(path.read_text()))
    # The bridge does not cancel the workflow, so b's job may still run on its node.
    b_product = ArtifactRegistry(base / "out", "run1").path_for("b.parquet")
    waited = time.time()
    while not b_product.exists() and time.time() - waited < 300:
        time.sleep(5)
    if b_product.exists():
        print(
            f"b's job kept running on its node and wrote b.parquet "
            f"{b_product.stat().st_mtime - cancelled:.0f} s after scancel"
        )
    else:
        print("b.parquet did not appear within 300 s of the controller ending")
    accounting = subprocess.run(
        ["sacct", "-j", b_job.split()[0], "-X", "-o", "JobID,JobName,State,Start,End"],
        capture_output=True,
        text=True,
    )
    print(accounting.stdout)


if ON_SLURM:
    kill_wait = re.search(
        r"KillWait\s*=\s*(\d+)",
        subprocess.run(["scontrol", "show", "config"], capture_output=True, text=True).stdout,
    )
    print(f"KillWait (Slurm's grace period): {kill_wait.group(1) if kill_wait else '?'} s\n")
    cancel_a_controller(WORK / "b6_exec", use_exec=True)
else:
    skipped()

# %%
if ON_SLURM:
    tasks, registry = lib.slow_chain(WORK / "b6_exec", pause=120)
    lib.show(build(tasks, WORK / "b6_exec" / "state", jobmon=True, **SLURM))
else:
    skipped()

# %%
if ON_SLURM:
    cancel_a_controller(WORK / "b6_no_exec", use_exec=False)
    tasks, registry = lib.slow_chain(WORK / "b6_no_exec", pause=120)
    lib.show(build(tasks, WORK / "b6_no_exec" / "state", jobmon=True, **SLURM))
else:
    skipped()

# %% [markdown]
# ## B7 — A step that runs out of memory is retried by Jobmon with more memory (cluster)
#
# The step touches 1.5 GB with a 1 GB limit, so Slurm kills its first attempt. Jobmon
# classifies that as a resource error and retries it with memory scaled up, and the
# retry succeeds. The build reports SUCCESS, and Jobmon's record shows both attempts.

# %%
if ON_SLURM:
    base = WORK / "b7"
    registry = lib._base(base, seed="seed", megabytes="1500")
    tasks = [
        lib.stable(
            python_step(
                lib.start,
                inputs={"seed": "seed"},
                produces="a.parquet",
                registry=registry,
                name="a",
            )
        ),
        lib.stable(
            python_step(
                lib.extend_holding_memory,
                inputs={"upstream": "a.parquet", "megabytes": "megabytes"},
                produces="hungry.parquet",
                registry=registry,
                name="hungry",
            )
        ),
    ]
    session = build(
        tasks,
        base / "state",
        jobmon=True,
        jobmon_max_attempts=3,
        jobmon_resource_scales={"memory": 1.0},
        **SLURM,
    )
    lib.show(session)
    state = session.config[RUN_STATE_KEY]
    print(f"\nJobmon workflow {state.workflow_id}")
    jobmon = shutil.which("jobmon") or str(Path(sys.executable).with_name("jobmon"))
    print(
        subprocess.run(
            [jobmon, "workflow_tasks", "-w", str(state.workflow_id)],
            capture_output=True,
            text=True,
        ).stdout[-3000:]
    )
else:
    skipped()

# %% [markdown]
# ## B8 — A second bridged build on the same root is refused, and the record is left as it is
#
# Fake: a child process holds the run lock, as a running bridged build does. The build
# is refused at once with a message naming the holder, runs nothing, and leaves
# `pytask.lock` byte for byte as it was. On the cluster the holder runs on another node,
# which shows the lock across hosts on NFS. The two-user drill (a second account) is
# pending; the review doc says so.

# %%
def hold_lock(root, *srun):
    """Start a process that holds the run lock on `root` until it is killed."""
    code = (
        "import sys, time; from pathlib import Path; "
        "from vivarium.dagger.backend.lock import run_lock; "
        f"cm = run_lock(Path({str(root / RUN_LOCK_NAME)!r})); cm.__enter__(); "
        "print('holding', flush=True); time.sleep(3600)"
    )
    # srun from inside this allocation would place the holder in it; a new allocation
    # needs the allocation's SLURM_ variables left out.
    env = {k: v for k, v in os.environ.items() if not (srun and k.startswith("SLURM_"))}
    holder = subprocess.Popen(
        [*srun, sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=env
    )
    assert holder.stdout.readline().strip() == "holding"
    return holder


base = WORK / "b8"
tasks, registry = lib.chain(base)
with FakeJobmon(workflow_id=801):
    build(tasks, base / "state", jobmon=True)
record = (base / "state" / "pytask.lock").read_bytes()
holder = hold_lock(base / "state")
try:
    with FakeJobmon(workflow_id=802) as fake:
        session = build(tasks, base / "state", jobmon=True)
finally:
    holder.kill()
print(
    "exit code:",
    session.exit_code.name,
    "| reports:",
    len(session.execution_reports),
    "| plans bound:",
    bound(fake),
)
print("refusal:", session.config.get(RUN_REFUSED_KEY))
print("pytask.lock unchanged:", (base / "state" / "pytask.lock").read_bytes() == record)

# %%
if ON_SLURM:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("SLURM_")}
    holder = hold_lock(
        base / "state",
        "srun",
        "-A",
        "proj_simscience",
        "-p",
        "all.q",
        "-c",
        "1",
        "--mem",
        "2G",
        "-t",
        "00:15:00",
        "--exclude",
        CONTROLLER,
    )
    try:
        session = build(tasks, base / "state", jobmon=True, **SLURM)
    finally:
        holder.kill()
    print("exit code:", session.exit_code.name, "| reports:", len(session.execution_reports))
    print("refusal:", session.config.get(RUN_REFUSED_KEY))
else:
    skipped()

# %% [markdown]
# ## B9 — Measured properties: plan time and dispatch overhead
#
# Plan time is the time from calling `build` to binding the Jobmon workflow, for N
# independent steps, measured with a fake that stops right after binding. Dispatch
# overhead is the gap between one step finishing on its node and the next step starting
# on another, read from the timestamps a fresh a -> b -> c chain records on Slurm. It
# includes Jobmon's polling and Slurm's queue wait, so it depends on how busy the
# partition is.

# %%
@contextmanager
def quiet():
    """Silence pytask's console while the block runs, so printing does not count as planning."""
    console.quiet = True
    try:
        with open(os.devnull, "w") as sink, redirect_stdout(sink):
            yield
    finally:
        console.quiet = False


class StopAfterBind(FakeJobmon):
    """Note when the bridge binds, then stop the run at once."""

    bind_times: list = []

    def bind(self, plan, **options):
        StopAfterBind.bind_times.append(time.perf_counter())
        return super().bind(plan, **options)


plan_times = {}
for n in (10, 100, 1000):
    base = WORK / f"b9_{n}"
    registry = lib._base(base, seed="seed")
    tasks = [
        lib.stable(
            python_step(
                lib.start,
                inputs={"seed": "seed"},
                produces=f"s{i}.parquet",
                registry=registry,
                name=f"s{i}",
            )
        )
        for i in range(n)
    ]
    StopAfterBind.bind_times = []
    started = time.perf_counter()
    with StopAfterBind(workflow_id=900 + n, error_after=0), quiet():
        build(tasks, base / "state", jobmon=True)
    plan_times[n] = StopAfterBind.bind_times[0] - started
print(pd.Series(plan_times, name="seconds from build() to bind").to_string())

# %%
if ON_SLURM:
    tasks, registry = lib.chain(WORK / "b9_dispatch")
    started = time.time()
    session = build(tasks, WORK / "b9_dispatch" / "state", jobmon=True, **SLURM)
    print(
        f"build of a -> b -> c plus d: {time.time() - started:.0f} s wall,",
        lib.outcome_names(session),
    )
    chain_rows = pd.read_parquet(registry.path_for("c.parquet"))
    gaps = (
        chain_rows["started"].iloc[1:].to_numpy()
        - chain_rows["finished"].iloc[:-1].to_numpy()
    )
    print(chain_rows[["step", "host", "started", "finished"]].to_string())
    print("gap from one step's end to the next step's start, per hop (s):", gaps.round(1))
else:
    skipped()

# %% [markdown]
# ## PLAY
#
# Cells for exploring on your own. Change the switch or the pause and rebuild, read a
# run's state file, or build with `jobmon_cluster="multiprocess"` to run the steps through
# real Jobmon without Slurm.

# %%
print(sorted(p.relative_to(WORK) for p in WORK.glob("*/state/.pytask/jobmon/*/state.json")))
