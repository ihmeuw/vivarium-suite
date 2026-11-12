# %% [markdown]
# # Track C walkthrough: the frontend and the `dagger` command
#
# This notebook is the evidence for the track C (frontend) validation review. Each section
# is headed by a criterion from the review's "Validation criteria" table, with the same ID
# and in the same order. Run it top to bottom: every outcome and timing below comes from
# your own run of the installed `dagger` command on this branch.
#
# Most sections drive `dagger` as a modeler would, as a separate process on a definition
# file in `c_walkthrough_defs/`. Sections marked "local" run anywhere; together they take
# about a minute on a laptop and a few minutes on a cluster node. Three sections are marked
# "cluster". C1-real runs MNCNH's own loaders and V&V notebook, in this process and then
# through Jobmon; it needs the MNCNH environments described there and skips itself when
# `C_WALKTHROUGH_MNCNH` is not set. C16 runs a definition through Jobmon with the `dagger`
# command; it skips itself outside a Slurm allocation or without Jobmon. C19 runs a toy
# simulation with psimulate; it skips itself outside a Slurm allocation or when
# `C_WALKTHROUGH_SIM_ENV` is not set. C14 and C15 are about the test suite, so the review
# doc shows them.
#
# Run it from `libs/dagger` with an env that has the `test` extra (papermill and
# ipykernel), as a notebook or with `python examples/c_frontend_walkthrough.py`. The
# cluster sections also need Jobmon in that env (the `cluster` extra, or a shared env that
# provides it), and `C_WALKTHROUGH_DIR` set to a folder on storage that every node can read
# and write, because steps that run through Jobmon write their products there.

# %% [markdown]
# ## How this notebook works
#
# The files a modeler writes are the definitions in `c_walkthrough_defs/`: `pipeline.py`,
# `mncnh_slice.py` and `toy_simulation.py`, with the notebook, scripts and model files they
# name. Almost every section runs the installed `dagger` command on one of them, in a
# separate process, the way a modeler would type it in a shell. The exceptions are C2's
# check that the command imports no Jobmon (it runs the command through click's test runner
# in a child Python), C12 (which runs the docs test with pytest) and C17 (which loads a
# definition in this process to print a step's command). Everything else in this notebook
# is machinery for showing many runs and many mistakes in one place. A modeler would not
# write any of it:
#
# - **A copy per section.** A definition keeps its outputs and pytask state next to itself
#   (`HERE / "output"`, `HERE / "state"`). If every section ran in one folder, each would
#   start from what the sections before it left. So each section works in its own copy of
#   `c_walkthrough_defs/` under a workspace folder, and the committed files never change.
# - **One-line edits.** Several sections need a definition that differs from the committed
#   one by a line: a misspelled key, a changed parameter, a step that raises. A modeler
#   would edit their file. Here `make_defs(name, edits=[(old, new)])` makes the copy,
#   applies the edit and prints it as `- old` / `+ new`, so you can see exactly what changed
#   before you read the message it causes.
# - **A primary-data handoff.** `write_handoff` writes the files an analyst would deliver
#   for each location.
# - **Running `dagger`.** `dagger(...)` runs the command, prints what a modeler would see in
#   the terminal with its exit status and time, and returns the result so the cell can check
#   it. `products` and `plan` read the output folder and the preview lines for those checks.
# - **Checks.** The `assert` lines turn the walkthrough into a test of what each section
#   claims. A modeler reads the output instead.
# - **`:stable`.** `-c pipeline.py:stable` loads a second function in the same definition
#   that gives every step the test harness's fixed code fingerprint, the stand-in for
#   MIC-7760. With the public API (`-c pipeline.py`) every step is stale on every run; C5
#   shows both.
# - **`--local`.** `dagger run` and `dagger force` send a definition's stale steps through
#   Jobmon unless `--local` is given. The local sections pass `--local` so that they run
#   anywhere in seconds; C16 and C1-real show the default on the cluster.
#
# The cluster sections add a little more machinery, explained where it appears: the
# bridge's run directories (`.pytask/jobmon/<run>/state.json`), read to print each Jobmon
# workflow's id; one addition to `PATH` for Jobmon's worker script (below); and, in C16, a
# run started in the background so that a second run can meet its lock and the first can
# be stopped with SIGTERM, as Slurm stops a job at the end of its time limit. The error
# table's case J1 takes `scontrol` off the command's `PATH` to stand in for a host where
# Jobmon cannot run.

# %% [markdown]
# Imports, and where things are: this example folder, the library, the installed `dagger`
# next to this Python, and the workspace (`C_WALKTHROUGH_DIR`, or a new temporary folder).
# The child processes get the notebook's environment without its colour settings, which
# would otherwise put pytask's live tables into the captured output.

# %%
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd

for EXAMPLES in (Path("examples").resolve(), Path.cwd()):
    if (EXAMPLES / "c_walkthrough_defs").is_dir():
        break
else:
    EXAMPLES = Path(__file__).resolve().parent
LIB = EXAMPLES.parent
DAGGER = Path(sys.executable).parent / "dagger"
WORK = Path(os.environ.get("C_WALKTHROUGH_DIR") or tempfile.mkdtemp(prefix="frontend_"))
WORK.mkdir(parents=True, exist_ok=True)
CHILD_ENV = {
    k: v
    for k, v in os.environ.items()
    if k not in {"FORCE_COLOR", "CLICOLOR_FORCE", "CLICOLOR"}
} | {"TERM": "dumb", "COLUMNS": "100"}
LOCATIONS = ["kenya", "nigeria"]


def commit():
    """Return the commit this walkthrough runs from."""
    out = subprocess.run(
        ["git", "-C", str(LIB), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
    )
    return out.stdout.strip() or "unknown"


print(f"workspace: {WORK}")
print(f"dagger: {DAGGER}")
print(f"python {sys.version.split()[0]} at {sys.executable}; commit {commit()}")


# %% [markdown]
# Copies of the definitions, the one-line edits, and the analyst's handoff.

# %%
def edit(path, pairs):
    """Apply (old, new) edits to the file at `path` and print each one; each old occurs once."""
    text = path.read_text()
    for old, new in pairs:
        assert text.count(old) == 1, f"{old!r} occurs {text.count(old)} times in {path.name}"
        text = text.replace(old, new)
        print(f"{path.name}: - {old.strip()}\n{' ' * len(path.name)}  + {new.strip()}")
    path.write_text(text)


def make_defs(name, edits=(), report_edits=()):
    """Copy the definitions into a new folder under WORK, apply one-line edits, and return it.

    Each edit is an (old, new) pair that must occur exactly once. The folder gets its own
    handoff, outputs and state, so no case changes another.
    """
    folder = WORK / name
    shutil.rmtree(folder, ignore_errors=True)
    shutil.copytree(EXAMPLES / "c_walkthrough_defs", folder)
    write_handoff(folder / "handoff")
    edit(folder / "pipeline.py", edits)
    edit(folder / "report.ipynb", report_edits)
    return folder


def write_handoff(root, values=(0.5, 0.7), locations=LOCATIONS):
    """Write each location's primary coverage file, as an analyst would deliver it."""
    for number, location in enumerate(locations):
        path = root / location / "primary" / "coverage.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            {"location_id": [number] * 2, "year_id": [2022, 2023], "coverage": list(values)}
        ).to_csv(path, index=False)



# %% [markdown]
# Running the installed `dagger` and reading what it left.

# %%
def dagger(*args, cwd, show=True, stdout=True, env=None):
    """Run the installed `dagger` with `args` in `cwd`, print what a modeler sees, and return it."""
    start = time.perf_counter()
    proc = subprocess.run(
        [str(DAGGER), *map(str, args)],
        cwd=cwd,
        env=CHILD_ENV | dict(env or {}),
        capture_output=True,
        text=True,
    )
    proc.seconds = time.perf_counter() - start
    if show:
        print(
            f"$ dagger {shlex.join(map(str, args))}    # exit {proc.returncode}, {proc.seconds:.1f} s"
        )
        if stdout and proc.stdout.strip():
            print(proc.stdout.rstrip())
        if proc.stderr.strip():
            print("[stderr]\n" + proc.stderr.rstrip())
        print()
    return proc


# A venv layered over a shared env, as build_mncnh_envs.sh makes, has Jobmon's console
# scripts only in the shared env's bin, and jobmon_slurm looks for its worker script on PATH
# or next to the Python that runs it. So the commands that go through Jobmon get the shared
# env's bin at the end of PATH. This is a recorded finding of the review, not something a
# modeler should need; the overlay venvs are being retired.
JOBMON_ENV = (
    {"PATH": CHILD_ENV["PATH"] + os.pathsep + str(Path(sys.base_prefix) / "bin")}
    if shutil.which("worker_node_entry_point", path=CHILD_ENV["PATH"]) is None
    else {}
)


def products(folder):
    """Return the files under a definition's output folder, relative to it."""
    out = folder / "output"
    return (
        sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
        if out.exists()
        else []
    )


def plan(proc):
    """Return the preview's run/skip lines as {step: line}."""
    lines = [l for l in proc.stdout.splitlines() if re.match(r"(run |skip) ", l)]
    return {l.split()[1].rstrip(":"): l for l in lines}


def bridged_runs(state):
    """Return the bridge's run directories under a pipeline's state folder."""
    runs = state / ".pytask" / "jobmon"
    return sorted(runs.iterdir()) if runs.exists() else []


def show_runs(runs):
    """Print the Jobmon workflow id and status that each bridge run directory records."""
    for run_dir in runs:
        state = json.loads((run_dir / "state.json").read_text())
        print(
            f"Jobmon workflow {state['workflow_id']}, status {state['workflow_status']} "
            f"({state['status']}), run directory {run_dir.name}"
        )
    if not runs:
        print("No Jobmon workflow was bound.")


# %% [markdown]
# ## C1 — One definition declares the steps; custom steps are first-class (local)
#
# The definition below has python steps, a fan-out over a two-location handoff, a
# notebook step, and a command step, all wired by artifact key through one
# registry. No file path appears in the wiring. Loading the definition runs nothing, so
# `preview` plans the whole DAG before any product exists, and names every file by its key.

# %%
GOOD = make_defs("good")
print((GOOD / "pipeline.py").read_text())

# %%
first = dagger("preview", "-c", "pipeline.py:stable", cwd=GOOD)
assert first.returncode == 0 and products(GOOD) == []
assert set(plan(first)) == {
    "population",
    "adults",
    "summary",
    "coverage_kenya",
    "coverage_nigeria",
    "report",
    "archive",
}
assert str(WORK) not in first.stdout, "a reason shows an absolute path"

# %% [markdown]
# ## C2 — One installed command (local)
#
# `dagger --help` lists the verbs, and `dagger run --help` shows the options a run through
# Jobmon takes. There is no `vwf` script. Neither a preview nor a run with `--local`
# imports Jobmon; each is checked in its own copy, so the run's products stay out of the
# folder the next sections use.

# %%
dagger("--help", cwd=GOOD)
dagger("run", "--help", cwd=GOOD)
assert not (DAGGER.parent / "vwf").exists()
for verb in (["preview"], ["run", "--local"]):
    folder = make_defs("c2_" + verb[0])
    probe = (
        "import sys; from click.testing import CliRunner; from vivarium.dagger.cli import dagger; "
        f"r = CliRunner().invoke(dagger, [{verb[0]!r}, '-c', 'pipeline.py:stable', *{verb[1:]!r}]); "
        "print('exit', r.exit_code, 'jobmon imported:', any(m.split('.')[0] == 'jobmon' for m in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], cwd=folder, env=CHILD_ENV, capture_output=True, text=True
    )
    print(" ".join(verb) + ":", out.stdout.strip())
    assert "exit 0 jobmon imported: False" in out.stdout, out.stdout + out.stderr

# %% [markdown]
# ## C3 — Exit status: 0 success, 1 a failed step, 2 a wrong definition or command (local)
#
# `dagger run` and `dagger force` send a definition's stale steps through Jobmon unless
# `--local` is given. C16 shows that on the cluster, including a failing run whose exit
# status gates the next command; this section stays local so that it runs anywhere.
# Exit 2 means nothing ran: a wrong definition or command, a host where Jobmon cannot run
# (case J1 in the error table), or a step with no project (C16). Exit 1 means a step failed,
# or another run held the run lock (C16). 130 is an interrupt; for a run through Jobmon the
# message names the workflow, which is not cancelled (C16).
#
# The first command gives a Jobmon option together with `--local`, which is refused before
# the definition is loaded. Then a failed step exits 1, so the status can gate the next
# command in a shell. The failing definition here is C9's.

# %%
wrong = dagger("run", "-c", "pipeline.py:stable", "--local", "-P", "proj_simscience", cwd=GOOD)
assert wrong.returncode == 2, wrong.returncode
assert "--project" in wrong.stderr and "not with --local" in wrong.stderr
assert products(GOOD) == []

FAILS = make_defs(
    "c3_fails", edits=[('frame["age_group"] != "0-14"', 'frame["age"] != "0-14"')]
)
line = f"{shlex.quote(str(DAGGER))} run -c pipeline.py:stable --local > /dev/null 2>&1 && echo 'next command ran' || echo \"stopped: dagger exited $?\""
for folder in (GOOD, FAILS):
    chain = subprocess.run(
        ["bash", "-c", line], cwd=folder, env=CHILD_ENV, capture_output=True, text=True
    )
    print(f"{folder.name}: {chain.stdout.strip()}")
    assert chain.stdout.strip() == (
        "next command ran" if folder is GOOD else "stopped: dagger exited 1"
    )

# %% [markdown]
# ## C4 — `--until S` runs S and its ancestors only (local)
#
# From an empty state, `run --until adults` makes population and adults and nothing else.
# A preview limited the same way marks the other steps "not selected".

# %%
UNTIL = make_defs("c4_c5")
dagger("run", "-c", "pipeline.py:stable", "--local", "--until", "adults", cwd=UNTIL)
print("products:", products(UNTIL))
assert products(UNTIL) == ["walk/adults.parquet", "walk/population.parquet"]
limited = dagger("preview", "-c", "pipeline.py:stable", "--until", "adults", cwd=UNTIL)
assert "not selected" in plan(limited)["summary"]

# %% [markdown]
# ## C5 — A plain `run` then executes only what is left (local; uses the stand-in)
#
# Every step built with the public API is always stale until MIC-7760 gives steps a code
# fingerprint. This section uses `pipeline.py:stable`, whose steps carry the harness's
# `stable_code_id` stand-in, as track B's B2 did. The second half shows the public API
# today: after the same `--until`, every step would run again.

# %%
before = dagger("preview", "-c", "pipeline.py:stable", cwd=UNTIL)
assert {s for s, l in plan(before).items() if l.startswith("skip")} == {
    "population",
    "adults",
}
rest = dagger("run", "-c", "pipeline.py:stable", "--local", cwd=UNTIL)
assert rest.returncode == 0
after = dagger("preview", "-c", "pipeline.py:stable", cwd=UNTIL)
assert all(l.startswith("skip") for l in plan(after).values()), after.stdout

# %%
dagger("run", "-c", "pipeline.py", "--local", "--until", "adults", cwd=UNTIL, stdout=False)
public = dagger("preview", "-c", "pipeline.py", cwd=UNTIL)
assert all(l.startswith("run") for l in plan(public).values())

# %% [markdown]
# ## C6 — `force S` re-runs S; its descendants run only if its output changed (local)
#
# Patrick confirmed this rule on 2026-11-09. Forcing adults, whose output comes out the
# same, runs adults alone. A copy of the definition next to it, `stamped.py`, has a
# population step that also writes the time it ran, so forcing it changes
# population.parquet: adults runs again, but adults drops that column, so its output is
# unchanged and summary and later steps stay skipped.
#
# The rule holds for a run in this process (`--local`), which this section uses. Through
# Jobmon, the bridge submits every step after a forced or stale step even when that step's
# inputs did not change; early cutoff on the bridge is MIC-7763. C16 forces a step through
# Jobmon.

# %%
FORCE = UNTIL
same = dagger("force", "-c", "pipeline.py:stable", "--local", "adults", cwd=FORCE)
assert same.returncode == 0
assert all(
    l.startswith("skip")
    for l in plan(
        dagger("preview", "-c", "pipeline.py:stable", cwd=FORCE, show=False)
    ).values()
)
digest = {
    p: hashlib.sha256((FORCE / "output" / p).read_bytes()).hexdigest()[:12]
    for p in products(FORCE)
}
# A second definition file next to the first shares its outputs and state.
stamped = (FORCE / "pipeline.py").read_text()
for old, new in (
    ("import sys\n", "import sys\nimport time\n"),
    ('"count": [30, 55, 15]})', '"count": [30, 55, 15]}).assign(made_at=time.time())'),
):
    assert stamped.count(old) == 1, old
    stamped = stamped.replace(old, new)
(FORCE / "stamped.py").write_text(stamped)
changed = dagger("force", "-c", "stamped.py:stable", "--local", "population", cwd=FORCE)
assert changed.returncode == 0
now = {
    p: hashlib.sha256((FORCE / "output" / p).read_bytes()).hexdigest()[:12]
    for p in products(FORCE)
}
print("bytes changed:", sorted(p for p in now if now[p] != digest[p]))
assert sorted(p for p in now if now[p] != digest[p]) == ["walk/population.parquet"]

# %%
assert dagger("force", "-c", "pipeline.py:stable", "--local", cwd=FORCE).returncode == 2
assert (
    dagger(
        "force", "-c", "pipeline.py:stable", "--local", "adult", "sumary", cwd=FORCE
    ).returncode
    == 2
)

# %% [markdown]
# ## C10 — The primary-data handoff (local)
#
# Each location's file sits at `<root>/<location>/primary/coverage.csv` and is checked
# before anything runs. When the analyst delivers a new Nigeria file, preview marks only
# that location's step, and the run reads the new values.

# %%
HANDOFF = GOOD
dagger("run", "-c", "pipeline.py:stable", "--local", cwd=HANDOFF, stdout=False)
write_handoff(HANDOFF / "handoff", values=(0.6, 0.8), locations=["nigeria"])
redelivered = dagger("preview", "-c", "pipeline.py:stable", cwd=HANDOFF)
assert {s for s, l in plan(redelivered).items() if l.startswith("run")} == {
    "coverage_nigeria"
}
dagger("run", "-c", "pipeline.py:stable", "--local", cwd=HANDOFF, stdout=False)
print(pd.read_parquet(HANDOFF / "output/walk/nigeria/mean_coverage.parquet"))

# %% [markdown]
# ## C11 — The notebook step (local)
#
# The report notebook ran in the declared env and wrote its product and an executed copy.
# The source notebook is unchanged. A changed parameter marks the step in preview. Its
# parameters cell assigns `summary`, `report` and `title`, the names the step passes, so
# the parameters-cell check (C18) accepts it; the two cases it refuses are in the
# plain-errors table below.

# %%
executed = json.loads((HANDOFF / "output/walk/report_executed.ipynb").read_text())
print(
    "executed cells:",
    [c.get("execution_count") for c in executed["cells"] if c["cell_type"] == "code"],
)
print(pd.read_csv(HANDOFF / "output/walk/report.csv"))
source = json.loads((HANDOFF / "report.ipynb").read_text())
assert all(not c.get("outputs") for c in source["cells"]), "the source notebook was changed"
# A second definition file next to the first shares its outputs and state.
retitled = HANDOFF / "retitled.py"
retitled.write_text(
    (HANDOFF / "pipeline.py")
    .read_text()
    .replace('"title": "Adults"', '"title": "Adults 15+"')
)
param = dagger("preview", "-c", "retitled.py:stable", cwd=HANDOFF)
assert {s for s, l in plan(param).items() if l.startswith("run")} == {"report", "archive"}

# %% [markdown]
# ## C17 — A command step runs a shell command with its paths filled in (local)
#
# The archive step is `command_step("cp {report} {copy}", ...)`. Each `{name}` becomes
# the absolute path of the input or product of that name, quoted for the shell, and the
# product's folder is made before the command runs. The cell prints the command the step
# ran, with this workspace's path shortened, and checks that the copy matches the report.
# C1-real's artifact step is a command step that runs a script in the model's artifact
# env. A misspelled placeholder and a failing command are in the plain-errors table below.

# %%
from vivarium.dagger.frontend import load_pipeline

archive = next(
    t for t in load_pipeline(f"{HANDOFF / 'pipeline.py'}:stable").tasks if t.name == "archive"
)
print(archive.run.replace(str(WORK), "<workspace>"))
copy, report = HANDOFF / "output/walk/archive/report.csv", HANDOFF / "output/walk/report.csv"
assert copy.read_bytes() == report.read_bytes()

# %% [markdown]
# ## C8, C9, C13, C17, C18 — Plain errors (local)
#
# Each case below makes one deliberate mistake in a fresh copy of the definition, runs the
# installed `dagger`, and prints the exit code and the error output verbatim, with the
# edit that caused it. The checks assert what a machine can: the exit code, the names the
# message must carry, and, for mistakes in definition code, that no `vivarium/dagger/`
# frame is shown. Whether each message says what to fix is the reviewer's verdict, in the
# table at the end.

# %%
CASES = []


def show_error(
    case,
    *,
    edits=(),
    report_edits=(),
    args=("preview", "-c", "pipeline.py:stable"),
    setup=None,
    env=None,
    exit_code,
    names=(),
    absent=(),
    user_frames_only=False,
):
    """Run one deliberate mistake, print what the modeler sees, and check it."""
    print(f"### {case}")
    folder = make_defs(f"err_{len(CASES):02d}", edits=edits, report_edits=report_edits)
    if setup:
        setup(folder)
    proc = dagger(*args, cwd=folder, stdout=False, env=env)
    stdout_tail = "\n".join(proc.stdout.rstrip().splitlines()[-12:])
    if proc.returncode == 1 and stdout_tail:
        print("[end of pytask's report on stdout]\n" + stdout_tail + "\n")
    problems = (
        [f"exit {proc.returncode}, wanted {exit_code}"]
        if proc.returncode != exit_code
        else []
    )
    problems += [f"missing {n!r}" for n in names if n not in proc.stderr]
    problems += [f"shows {a!r}" for a in absent if a in proc.stderr]
    if user_frames_only and "vivarium/dagger/" in proc.stderr:
        problems.append("shows a dagger frame")
    CASES.append(
        {
            "case": case,
            "command": "dagger " + shlex.join(args),
            "exit": proc.returncode,
            "checks": "; ".join(problems) or "pass",
            "chars": len(proc.stderr),
        }
    )
    print("checks:", CASES[-1]["checks"], "\n")
    return folder, proc


def break_handoff(folder):
    """Drop Kenya's file and rename a column in Nigeria's."""
    (folder / "handoff/kenya/primary/coverage.csv").unlink()
    path = folder / "handoff/nigeria/primary/coverage.csv"
    path.write_text(path.read_text().replace("year_id", "year", 1))


show_error(
    "W1 input key typo",
    edits=[('inputs={"adults": "adults.parquet"}', 'inputs={"adults": "adult.parquet"}')],
    exit_code=2,
    names=["summary", "adult.parquet", "adults.parquet"],
    user_frames_only=True,
)
show_error(
    "W2 input key with no close match",
    edits=[('inputs={"adults": "adults.parquet"}', 'inputs={"adults": "births.csv"}')],
    exit_code=2,
    names=["summary", "births.csv", "population.parquet"],
    user_frames_only=True,
)
show_error(
    "W3 notebook input key typo",
    edits=[('inputs={"summary": "summary.csv"}', 'inputs={"summary": "sumary.csv"}')],
    exit_code=2,
    names=["report", "sumary.csv", "summary.csv"],
    user_frames_only=True,
)
show_error(
    "W7 two steps claim one artifact",
    edits=[
        (
            'produces="summary.csv",',
            'produces="summary.csv",\n            registry=registry,\n            name="summary_v1",\n        ),\n        python_step(\n            total_adults,\n            inputs={"adults": "adults.parquet"},\n            produces="summary.csv",',
        )
    ],
    exit_code=2,
    names=["summary.csv", "summary_v1", "summary"],
)
show_error(
    "W8 two steps share a name",
    edits=[('name=f"coverage_{location}"', 'name="coverage"')],
    exit_code=2,
    names=["coverage", "name="],
    absent=["['coverage']"],
)
show_error(
    "P1 a command placeholder typo",
    edits=[('"cp {report} {copy}"', '"cp {reprot} {copy}"')],
    exit_code=2,
    names=["archive", "{reprot}", "Did you mean 'report'?"],
)
show_error(
    "N1 a parameter the notebook's parameters cell does not assign",
    edits=[('parameters={"title": "Adults"}', 'parameters={"titel": "Adults"}')],
    exit_code=2,
    names=["report", "'titel'", "did you mean 'title'?"],
)
show_error(
    "N2 a notebook with no parameters cell",
    report_edits=[('"tags":["parameters"]', '"tags":[]')],
    exit_code=2,
    names=["report", "report.ipynb", "parameters"],
)
show_error(
    "H1+H2 handoff: a missing file and a renamed column",
    setup=break_handoff,
    exit_code=2,
    names=["kenya", "coverage.csv", "nigeria", "year_id"],
    absent=["Traceback"],
)


def widen_handoff(folder):
    """Give Nigeria's file 300 draw columns, as real model inputs have, and rename year_id."""
    path = folder / "handoff/nigeria/primary/coverage.csv"
    frame = pd.read_csv(path).rename(columns={"year_id": "year"})
    frame = pd.concat(
        [frame, pd.DataFrame({f"draw_{i}": [0.5, 0.6] for i in range(300)})], axis=1
    )
    frame.to_csv(path, index=False)


_, wide = show_error(
    "H2 a wide handoff file is missing a column",
    setup=widen_handoff,
    exit_code=2,
    names=["nigeria", "year_id"],
    absent=["draw_299"],
)
assert len(wide.stderr) < 1500, f"the message is {len(wide.stderr)} characters"
FAILED_FOLDER, _ = show_error(
    "T2 a python step raises",
    edits=[('frame["age_group"] != "0-14"', 'frame["age"] != "0-14"')],
    args=("run", "-c", "pipeline.py:stable", "--local"),
    exit_code=1,
    names=["adults", "KeyError", "summary", "report", "archive"],
)
print(
    "the independent branch completed:",
    [p for p in products(FAILED_FOLDER) if "coverage" in p],
)
assert any("nigeria/mean_coverage" in p for p in products(FAILED_FOLDER))
show_error(
    "T4 a notebook raises",
    report_edits=[("frame['adults'][0]", "frame['adult'][0]")],
    args=("run", "-c", "pipeline.py:stable", "--local"),
    exit_code=1,
    names=["report", "KeyError", "adult", "report_executed.ipynb", "In ["],
    absent=["papermill", "ipykernel install"],
)
show_error(
    "Command step fails",
    edits=[('"cp {report} {copy}"', '"false && cp {report} {copy}"')],
    args=("run", "-c", "pipeline.py:stable", "--local"),
    exit_code=1,
    names=["archive", "status 1"],
    absent=["&& cp"],
)
show_error(
    "T6 declared env does not exist",
    edits=[("env=sys.prefix", 'env="vivarium_no_such_env"')],
    args=("run", "-c", "pipeline.py:stable", "--local"),
    exit_code=2,
    names=["report", "vivarium_no_such_env"],
)
show_error(
    "T7 a step calls sys.exit",
    edits=[
        (
            '"""Return a small population table by age group."""',
            'sys.exit("no census this year")',
        )
    ],
    args=("run", "-c", "pipeline.py:stable", "--local"),
    exit_code=1,
    names=["population"],
)
show_error(
    "T1 --until names an unknown step",
    args=("run", "-c", "pipeline.py:stable", "--local", "--until", "adult"),
    exit_code=2,
    names=["adult", "adults", "Valid steps"],
)
show_error(
    "D6 definition code raises on import",
    edits=[("import pandas as pd", "import pandsa as pd")],
    exit_code=2,
    names=["pipeline.py", "pandsa", "Could not load"],
    user_frames_only=True,
)
show_error(
    "D7 syntax error in the definition",
    edits=[
        (
            "def total_adults(adults: Path) -> pd.DataFrame:",
            "def total_adults(adults: Path) -> pd.DataFrame",
        )
    ],
    exit_code=2,
    names=["pipeline.py", "SyntaxError", "Could not load"],
    user_frames_only=True,
)
# J1 stands in for a login node or a laptop: scontrol is taken off the command's PATH.
# On the cluster the message says scontrol is missing and to run on a compute node; in an
# env without Jobmon it says to install vivarium-dagger[cluster]. Both end by offering
# --local, and nothing is written.
NO_SCONTROL_PATH = os.pathsep.join(
    d for d in CHILD_ENV["PATH"].split(os.pathsep) if not (Path(d) / "scontrol").exists()
)
_, no_jobmon = show_error(
    "J1 a run through Jobmon where Jobmon cannot run",
    args=("run", "-c", "pipeline.py:stable", "-P", "proj_simscience"),
    env={"PATH": NO_SCONTROL_PATH},
    exit_code=2,
    names=["Jobmon", "--local"],
)

# %% [markdown]
# After the cause in T2 is fixed, the same command completes. Only the failed step and
# the steps it blocked run.

# %%
fixed = FAILED_FOLDER / "pipeline.py"
fixed.write_text(
    fixed.read_text().replace('frame["age"] != "0-14"', 'frame["age_group"] != "0-14"')
)
again = dagger("run", "-c", "pipeline.py:stable", "--local", cwd=FAILED_FOLDER, stdout=False)
assert again.returncode == 0
assert all(
    l.startswith("skip")
    for l in plan(
        dagger("preview", "-c", "pipeline.py:stable", cwd=FAILED_FOLDER, show=False)
    ).values()
)

# %%
columns = ["case", "command", "exit", "chars", "checks"]
print("| " + " | ".join(columns) + " | says what to fix (reviewer) |")
print("|" + "---|" * (len(columns) + 1))
for row in CASES:
    print(
        "| "
        + " | ".join(f"`{row[c]}`" if c == "command" else str(row[c]) for c in columns)
        + " |  |"
    )
failed = [row["case"] for row in CASES if row["checks"] != "pass"]
assert not failed, failed

# %% [markdown]
# ## C7 — Preview speaks in keys and step names (local)
#
# Every preview above named files by artifact key and steps by name. This cell checks
# all of them at once: no line holds an absolute path from this workspace.

# %%
for proc in (first, limited, before, after, public, redelivered, param):
    for line in plan(proc).values():
        assert str(WORK) not in line, line
print(
    "checked",
    sum(len(plan(p)) for p in (first, limited, before, after, public, redelivered, param)),
    "preview lines",
)

# %% [markdown]
# ## C12 — Every command in the docs page runs (local)
#
# `docs/source/python_pipelines.rst` shows each subcommand on a worked example. The test
# below runs every `$ dagger` line on that page in order and fails if one exits non-zero.
# It runs them under the test harness's fake Jobmon, so the page's example of a run through
# Jobmon (`dagger run -c pipeline.py -P ...`) runs through the bridge without a cluster;
# C16 is the real run.

# %%
docs = subprocess.run(
    [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "tests/frontend/test_cli.py::test_every_command_in_the_docs_page_exits_zero",
    ],
    cwd=LIB,
    env=CHILD_ENV,
    capture_output=True,
    text=True,
)
print(docs.stdout.strip().splitlines()[-1])
assert docs.returncode == 0

# %% [markdown]
# ## C1-real — A real model pipeline: MNCNH's loaders, a Nigeria artifact, and its V&V notebook (cluster)
#
# `c_walkthrough_defs/mncnh_slice.py` turns part of MNCNH's artifact build into steps:
# three python steps call the model's own loader (`postpartum_fraction`, `demography`,
# and `ifa_coverage`, which reads the analyst's Nigeria extract through the handoff); a
# command step runs `write_artifact.py` in the model's artifact env to write them into
# `nigeria.hdf`, as `make_artifacts` does; and a notebook step runs the model's
# `hemorrhage_split_proportions.ipynb` V&V notebook on that artifact in the same env.
#
# It needs the GBD database, `/snfs1`, and two venvs over the Jenkins
# `vivarium_gates_mncnh_artifact_current` shared env in an MNCNH checkout:
# `.venv/vivarium_gates_mncnh_artifact` for the notebook, and the env running this
# walkthrough, which must import `vivarium_gates_mncnh` and this branch's dagger;
# `c_walkthrough_defs/build_mncnh_envs.sh` builds both on a compute node. Set
# `C_WALKTHROUGH_MNCNH` to the checkout and `DAGGER_SLICE_ROOT` to a new directory, and
# run on a compute node. The model's notebook has no `parameters` cell, and
# `notebook_step` refuses such a notebook when the step passes it names (C18), so the demo
# copy adds one; that change is what a modeler makes to adopt `notebook_step`.
# The section ends with two mistakes on the real slice, each made in a copy of the
# definition: a misspelled handoff file name, and a handoff whose IFA file renamed
# `year_id` (1007 columns, as the real extract has).
#
# The run and the skip use `mncnh_slice.py:stable`, which carries the `stable_code_id`
# stand-in, and run in this process (`--local`). Then the public API (`mncnh_slice.py`),
# which has its own state, runs the slice again through Jobmon, the command's default: the
# step functions are imported on the nodes from a definition file outside the folder
# `dagger` runs from, the artifact and notebook steps run in the model's env there, and the
# slice's `Pipeline(resources=...)` gives every step more memory than dagger's default. The
# project comes from `-P`. A preview after it shows that every step would run again until
# MIC-7760.

# %%
MNCNH = os.environ.get("C_WALKTHROUGH_MNCNH")
if not MNCNH:
    print("skipped: C_WALKTHROUGH_MNCNH is not set")
else:
    SLICE = Path(os.environ["DAGGER_SLICE_ROOT"])
    DEF = EXAMPLES / "c_walkthrough_defs" / "mncnh_slice.py"
    if not (SLICE / "handoff/Nigeria/primary/ifa_coverage.csv").exists():
        subprocess.run(
            [
                sys.executable,
                EXAMPLES / "c_walkthrough_defs/ifa_extract.py",
                SLICE / "handoff",
            ],
            check=True,
        )
    subprocess.run(
        [
            sys.executable,
            EXAMPLES / "c_walkthrough_defs/make_demo_notebook.py",
            MNCNH,
            SLICE / "notebooks/hemorrhage_split_proportions.ipynb",
        ],
        check=True,
    )
    for d in ("output", "state", "state_stable"):
        shutil.rmtree(SLICE / d, ignore_errors=True)
    print(DEF.read_text())
    dagger("preview", "-c", f"{DEF}:stable", cwd=MNCNH)
    real = dagger("run", "-c", f"{DEF}:stable", "--local", cwd=MNCNH)
    assert real.returncode == 0
    print("products:", products(SLICE))
    assert all(
        l.startswith("skip")
        for l in plan(dagger("preview", "-c", f"{DEF}:stable", cwd=MNCNH)).values()
    )
    nb = json.loads(
        (
            SLICE / "output/nigeria/notebooks/hemorrhage_split_proportions_executed.ipynb"
        ).read_text()
    )
    for cell in nb["cells"]:
        lines = "".join(cell["source"]).splitlines()
        if (
            cell["cell_type"] == "code"
            and lines
            and lines[-1] in {"base_artifacts_dir", "artifact_paths"}
        ):
            print(
                lines[-1],
                "->",
                "".join(
                    "".join(o.get("data", {}).get("text/plain", "")) for o in cell["outputs"]
                ),
            )
    real_jobmon = dagger("run", "-c", DEF, "-P", "proj_simscience", cwd=MNCNH, env=JOBMON_ENV)
    show_runs(bridged_runs(SLICE / "state"))
    assert real_jobmon.returncode == 0
    assert len(bridged_runs(SLICE / "state")) == 1
    real_public = dagger("preview", "-c", DEF, cwd=MNCNH)
    assert all(l.startswith("run") for l in plan(real_public).values())

    mistakes = WORK / "slice_mistakes"
    mistakes.mkdir(exist_ok=True)
    source = DEF.read_text()
    typo = 'handoff_key(LOCATION, "ifa_coverage.csv")'
    assert source.count(typo) == 1
    key = mistakes / "mncnh_slice_misspelled_key.py"
    key.write_text(source.replace(typo, 'handoff_key(LOCATION, "ifa_coverge.csv")'))
    misspelled = dagger("preview", "-c", f"{key}:stable", cwd=MNCNH)
    assert misspelled.returncode == 2 and "ifa_coverage.csv" in misspelled.stderr

    renamed_csv = SLICE / "handoff_renamed/Nigeria/primary/ifa_coverage.csv"
    renamed_csv.parent.mkdir(parents=True, exist_ok=True)
    renamed_csv.write_text(
        (SLICE / "handoff/Nigeria/primary/ifa_coverage.csv")
        .read_text()
        .replace("year_id", "year", 1)
    )
    root = 'SLICE_ROOT / "handoff"'
    assert source.count(root) == 1
    column = mistakes / "mncnh_slice_renamed_column.py"
    column.write_text(source.replace(root, 'SLICE_ROOT / "handoff_renamed"'))
    renamed = dagger("preview", "-c", f"{column}:stable", cwd=MNCNH)
    assert renamed.returncode == 2 and "'year_id' may be 'year'" in renamed.stderr
    assert len(renamed.stderr) < 1500, len(renamed.stderr)

# %% [markdown]
# ## C16 — A definition runs through Jobmon from the `dagger` command (cluster)
#
# Without `--local`, `dagger run` and `dagger force` send a definition's stale steps to
# Slurm as one Jobmon workflow. Each step's compute resources start from dagger's defaults
# (`vivarium.dagger.constants.DEFAULT_RESOURCES`: all.q, 1 GB, an hour, one core), then the
# definition's `Pipeline(resources=...)`, then the command's `-P`, `-q` and `-m`, then the
# step's own `resources=`. The walkthrough's definition sets no project, as an example
# shared between people should not, so each command passes `-P`. A node imports each python
# step's function through the import root that the bridge sends with the step, so nothing
# sets `PYTHONPATH`: the commands run from the workspace folder, not the definition's, and
# the cell checks that `PYTHONPATH` is unset and that no installed module is named
# `pipeline`.
#
# In order, each in its own copy where it says so:
#
# 1. The command with no project exits 2 and writes nothing.
# 2. The good copy runs through Jobmon; a preview then skips every step, and the same
#    command again binds no workflow.
# 3. `dagger force ... adults` runs through Jobmon. The bridge also submits the steps after
#    adults, because it has no early cutoff (C6, MIC-7763).
# 4. A copy of the definition next to it, `failing_archive.py`, whose archive command fails
#    on its node: exit 1, and dagger's line for the step.
# 5. A broken copy, in which a python step and the notebook fail, runs with `-m 1` behind
#    the same shell gate as C3: "stopped: dagger exited 1", each failure named by step, and
#    the node's traceback in pytask's report.
# 6. In its own copy, whose population step sleeps for three minutes: a second run while
#    the first holds the run lock is refused (exit 1, the lock file and its holder); SIGTERM
#    to the first, as Slurm sends at the end of a job's time limit, ends it with exit 130
#    and its workflow id; and the same command then runs again to the end.

# %%
import importlib.util
import signal

ON_SLURM = "SLURM_JOB_ID" in os.environ and importlib.util.find_spec("jobmon") is not None
PROJECT = "proj_simscience"


def jobmon_dagger(*args, **kwargs):
    """Run `dagger` from the workspace with the PATH that the Jobmon runs need."""
    return dagger(*args, cwd=WORK, env=JOBMON_ENV, **kwargs)


if not ON_SLURM:
    print("skipped: not in a Slurm allocation, or Jobmon is not installed")
else:
    if JOBMON_ENV:
        print(f"Jobmon's worker script is not on PATH; the commands add {JOBMON_ENV['PATH'].rsplit(os.pathsep, 1)[1]}")
    assert "PYTHONPATH" not in CHILD_ENV, "C16 shows that a node imports steps without it"
    clash = subprocess.run(
        [sys.executable, "-I", "-c", "import importlib.util as u; print(u.find_spec('pipeline'))"],
        cwd=WORK,
        env=CHILD_ENV,
        capture_output=True,
        text=True,
    )
    print("PYTHONPATH is not set; an installed module named 'pipeline':", clash.stdout.strip())
    assert clash.stdout.strip() == "None"

    # 1. No project.
    GOOD_JOBMON = make_defs("jobmon_good")
    GOOD_DEF, GOOD_STATE = "jobmon_good/pipeline.py:stable", GOOD_JOBMON / "state_stable"
    no_project = jobmon_dagger("run", "-c", GOOD_DEF)
    assert no_project.returncode == 2 and "-P/--project" in no_project.stderr
    assert products(GOOD_JOBMON) == [] and not bridged_runs(GOOD_STATE)

    # 2. The good copy, a preview, and the same command again.
    first = jobmon_dagger("run", "-c", GOOD_DEF, "-P", PROJECT)
    first_runs = bridged_runs(GOOD_STATE)
    show_runs(first_runs)
    assert first.returncode == 0 and len(first_runs) == 1
    print("products:", products(GOOD_JOBMON))
    assert len(products(GOOD_JOBMON)) == 8
    assert all(l.startswith("skip") for l in plan(jobmon_dagger("preview", "-c", GOOD_DEF)).values())
    second = jobmon_dagger("run", "-c", GOOD_DEF, "-P", PROJECT)
    assert second.returncode == 0 and bridged_runs(GOOD_STATE) == first_runs

    # 3. Force through Jobmon.
    forced = jobmon_dagger("force", "-c", GOOD_DEF, "-P", PROJECT, "adults")
    force_runs = [r for r in bridged_runs(GOOD_STATE) if r not in first_runs]
    show_runs(force_runs)
    assert forced.returncode == 0 and len(force_runs) == 1
    assert all(l.startswith("skip") for l in plan(jobmon_dagger("preview", "-c", GOOD_DEF)).values())

    # 4. A command step that fails on its node.
    shutil.copy(GOOD_JOBMON / "pipeline.py", GOOD_JOBMON / "failing_archive.py")
    edit(GOOD_JOBMON / "failing_archive.py", [('"cp {report} {copy}"', '"false && cp {report} {copy}"')])
    cmd_fail = jobmon_dagger("run", "-c", "jobmon_good/failing_archive.py:stable", "-P", PROJECT)
    assert cmd_fail.returncode == 1 and "Step 'archive' failed" in cmd_fail.stderr
    assert "status 1" in cmd_fail.stderr and "&& cp" not in cmd_fail.stderr

    # 5. A broken copy behind the shell gate.
    BROKEN_JOBMON = make_defs(
        "jobmon_broken",
        edits=[('pd.read_csv(coverage)["coverage"]', 'pd.read_csv(coverage)["coverag"]')],
        report_edits=[("frame['adults'][0]", "frame['adult'][0]")],
    )
    gate = (
        f"{shlex.quote(str(DAGGER))} run -c jobmon_broken/pipeline.py:stable -P {PROJECT} -m 1 "
        "> broken.out 2> broken.err && echo 'next command ran' || echo \"stopped: dagger exited $?\""
    )
    print(f"$ {gate}")
    chain = subprocess.run(
        ["bash", "-c", gate], cwd=WORK, env=CHILD_ENV | JOBMON_ENV, capture_output=True, text=True
    )
    print(chain.stdout.strip())
    assert chain.stdout.strip() == "stopped: dagger exited 1"
    broken_err, broken_out = (WORK / "broken.err").read_text(), (WORK / "broken.out").read_text()
    print("[stderr]\n" + broken_err.rstrip())
    print("[end of pytask's report on stdout]\n" + "\n".join(broken_out.rstrip().splitlines()[-25:]))
    show_runs(bridged_runs(BROKEN_JOBMON / "state_stable"))
    for expected in (
        "Step 'coverage_kenya' failed: KeyError: 'coverag'",
        "Step 'coverage_nigeria' failed: KeyError: 'coverag'",
        "Step 'report' failed: notebook report.ipynb raised KeyError: 'adult' in cell In [3]",
        "report_executed.ipynb",
    ):
        assert expected in broken_err, expected
    assert "Traceback" not in broken_err
    assert "mean_coverage" in broken_out, "the node's traceback is in pytask's report"
    # The archive step did not run, so its product is missing (its folder may exist).
    assert not (BROKEN_JOBMON / "output/walk/archive/report.csv").exists()

    # 6. The run lock and an interrupt.
    LOCKED = make_defs(
        "jobmon_lock",
        edits=[
            ("import sys\n", "import sys\nimport time\n"),
            (
                '"""Return a small population table by age group."""',
                '"""Return a small population table by age group."""\n    time.sleep(180)',
            ),
        ],
    )
    LOCK_DEF, LOCK_STATE = "jobmon_lock/pipeline.py:stable", LOCKED / "state_stable"
    argv = [str(DAGGER), "run", "-c", LOCK_DEF, "-P", PROJECT]
    print(f"$ dagger {shlex.join(argv[1:])} &    # started in the background")
    # Its output goes to files, which nothing has to read while it runs.
    held_out, held_err = WORK / "lock_holder.out", WORK / "lock_holder.err"
    with held_out.open("w") as out_file, held_err.open("w") as err_file:
        holder = subprocess.Popen(argv, cwd=WORK, env=CHILD_ENV | JOBMON_ENV, stdout=out_file, stderr=err_file)
    workflow_id, waited = None, time.perf_counter()
    while workflow_id is None:
        assert holder.poll() is None, "the first run ended before its workflow was bound"
        assert time.perf_counter() - waited < 1800, "no workflow was bound in 30 minutes"
        time.sleep(5)
        runs = bridged_runs(LOCK_STATE)
        if runs and (runs[-1] / "state.json").exists():
            workflow_id = json.loads((runs[-1] / "state.json").read_text()).get("workflow_id")
    print(f"the first run holds the lock; its Jobmon workflow is {workflow_id}")
    refused = jobmon_dagger("run", "-c", LOCK_DEF, "-P", PROJECT)
    assert refused.returncode == 1 and "run.lock" in refused.stderr, refused.stderr
    holder.send_signal(signal.SIGTERM)
    holder.wait(timeout=900)
    interrupted = held_err.read_text()
    print(f"$ kill -TERM <first run>    # the first run exits {holder.returncode}")
    print("[stderr]\n" + interrupted.rstrip())
    assert holder.returncode == 130, holder.returncode
    assert f"Jobmon workflow {workflow_id} was not cancelled" in interrupted
    assert "run the same command again" in interrupted
    resumed = jobmon_dagger("run", "-c", LOCK_DEF, "-P", PROJECT)
    show_runs(bridged_runs(LOCK_STATE))
    assert resumed.returncode == 0

# %% [markdown]
# ## C19 — A simulation step runs psimulate on the cluster (cluster)
#
# `c_walkthrough_defs/toy_simulation.py` has three steps. A command step writes a toy
# artifact; a simulation step runs the toy model (1,000 simulants, 2 draws and 2 seeds, so
# 4 simulations) with `psimulate run` on that artifact; and a python step sums the deaths by
# draw from the run directory, which it names by its key, `sims/toy`. The definition names
# its env `simulation`, which resolves to `.venv/simulation` in the folder `dagger` runs
# from, as MNCNH's envs do in its checkout. This section links that to the env named by
# `C_WALKTHROUGH_SIM_ENV`, which needs `psimulate` and Jobmon; the track B env has both.
# psimulate refuses hosts outside Slurm, so the section skips itself outside an allocation.
#
# It makes three runs from one copy, as MIC-7754 asks:
#
# 1. The toy run. Every step succeeds, and the run directory holds the 4 simulations'
#    results and metadata and the sentinel.
# 2. A failure drill. The copy's spec adds psimulate's fail-once component and its options
#    add `max_attempts=1`, so every simulation fails once and is not tried again. The step
#    fails with "0 of 4", run 1's directory is unchanged byte for byte, the partial run
#    stays in the scratch directory, and the summary does not run.
# 3. The same command again. The fail-once markers now exist, so every simulation
#    succeeds and the run directory is replaced.
#
# The drill also shows MIC-7799: the step's command names its check script through
# `$DAGGER_COLLECT_SIMULATION`, so pytask's report for the failed step no longer prints the
# script (385 lines in the earlier run).
#
# All three runs pass `--local`. Through Jobmon a simulation step would be one Jobmon task
# whose job starts psimulate's own workflow and holds a node for the whole run; MIC-7801
# owns that case and its evidence.

# %%
SIM_ENV = os.environ.get("C_WALKTHROUGH_SIM_ENV")


def tree(folder):
    """Return {relative path: sha256} for every file under `folder`."""
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


if not (SIM_ENV and "SLURM_JOB_ID" in os.environ):
    print("skipped: C_WALKTHROUGH_SIM_ENV is not set, or not in a Slurm allocation")
else:
    SIM = make_defs("simulation")
    (SIM / ".venv").mkdir()
    (SIM / ".venv" / "simulation").symlink_to(SIM_ENV)
    print((SIM / "toy_simulation.py").read_text())
    RUN, SUMMARY = SIM / "output/toy/sims/toy", SIM / "output/toy/summary.csv"

    toy = dagger("run", "-c", "toy_simulation.py", "--local", cwd=SIM)
    assert toy.returncode == 0
    first_run = tree(RUN)
    print("run directory:", *first_run, sep="\n  ")
    assert sum(f.startswith("results/dead/") for f in first_run) == 4
    assert sum(f.startswith("metadata/") and f.endswith(".json") for f in first_run) == 4
    print("sentinel:", (RUN / "_SUCCESS").read_text())
    print(pd.read_csv(SUMMARY))

    summary_written = SUMMARY.stat().st_mtime_ns
    edit(
        SIM / "toy_model.yaml",
        [
            (
                "      - DeathsObserver()\n",
                "      - DeathsObserver()\n"
                "  vivarium.psimulate.testing.fail_once_component:\n"
                "    - FailOnceComponent()\n",
            )
        ],
    )
    edit(SIM / "toy_simulation.py", [('"no_slack": True,', '"no_slack": True, "max_attempts": 1,')])
    markers = WORK / "fail_once_markers"
    markers.mkdir(exist_ok=True)
    fail_once = {"FAIL_ONCE_SENTINEL_DIR": str(markers)}
    drill = dagger("run", "-c", "toy_simulation.py", "--local", cwd=SIM, env=fail_once)
    assert drill.returncode == 1 and "0 of 4" in drill.stderr
    assert "DAGGER_COLLECT_SIMULATION" in drill.stdout, "pytask's report names the check's path"
    assert "def collect_run" not in drill.stdout + drill.stderr
    print("pytask's report for the drill:", len(drill.stdout.splitlines()), "lines")
    assert tree(RUN) == first_run, "run 1's directory changed"
    scratch = SIM / "output/toy/sims/toy.psimulate"
    print("scratch:", sorted(str(p.relative_to(scratch)) for p in scratch.glob("*/*")))
    assert any(scratch.glob("*/*"))
    assert SUMMARY.stat().st_mtime_ns == summary_written, "the summary ran"

    again = dagger("run", "-c", "toy_simulation.py", "--local", cwd=SIM, env=fail_once)
    assert again.returncode == 0
    third_run = tree(RUN)
    print("sentinel:", (RUN / "_SUCCESS").read_text())
    assert third_run["_SUCCESS"] != first_run["_SUCCESS"]
    print(pd.read_csv(SUMMARY))

# %% [markdown]
# ## PLAY
#
# Try your own: change a step in `GOOD / "pipeline.py"`, run `preview` to see what would
# run, then `run --local --until <step>` or `force --local <step>`. `make_defs("mine", edits=[...])`
# gives a fresh copy with one-line edits applied.

# %%
# mine = make_defs("mine", edits=[('"count": [30, 55, 15]', '"count": [30, 60, 15]')])
# dagger("preview", "-c", "pipeline.py:stable", cwd=mine)
