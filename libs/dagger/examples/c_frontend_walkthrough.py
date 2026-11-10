# %% [markdown]
# # Track C walkthrough: the frontend and the `dagger` command
#
# This notebook is the evidence for the track C (frontend) validation review. Each section
# is headed by a criterion from the review's "Validation criteria" table, with the same ID
# and in the same order. Run it top to bottom: every outcome and timing below comes from
# your own run of the installed `dagger` command on this branch.
#
# Every section drives `dagger` as a modeler would, as a separate process on a definition
# file in `c_walkthrough_defs/`. Sections marked "local" run anywhere in under a minute.
# The section marked "cluster" runs MNCNH's own loaders and V&V notebook. It needs the
# MNCNH environments described there and skips itself when `C_WALKTHROUGH_MNCNH` is not
# set. C14 and C15 are about the test suite, so the review doc shows them.
#
# Run it from `libs/dagger` with an env that has the `test` extra (papermill and
# ipykernel), as a notebook or with `python examples/c_frontend_walkthrough.py`.

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
# ipykernel sets these, and a child that inherits them draws pytask's live tables into
# the captured output.
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


def make_defs(name, edits=(), report_edits=()):
    """Copy the definitions into a new folder under WORK, apply one-line edits, and return it.

    Each edit is an (old, new) pair that must occur exactly once. The folder gets its own
    handoff, outputs and state, so no case changes another.
    """
    folder = WORK / name
    shutil.rmtree(folder, ignore_errors=True)
    shutil.copytree(EXAMPLES / "c_walkthrough_defs", folder)
    write_handoff(folder / "handoff")
    for file, pairs in (("pipeline.py", edits), ("report.ipynb", report_edits)):
        path = folder / file
        text = path.read_text()
        for old, new in pairs:
            assert text.count(old) == 1, f"{old!r} occurs {text.count(old)} times in {file}"
            text = text.replace(old, new)
            print(f"{file}: - {old.strip()}\n{' ' * len(file)}  + {new.strip()}")
        path.write_text(text)
    return folder


def write_handoff(root, values=(0.5, 0.7), locations=LOCATIONS):
    """Write each location's primary coverage file, as an analyst would deliver it."""
    for number, location in enumerate(locations):
        path = root / location / "primary" / "coverage.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            {"location_id": [number] * 2, "year_id": [2022, 2023], "coverage": list(values)}
        ).to_csv(path, index=False)


def dagger(*args, cwd, show=True, stdout=True):
    """Run the installed `dagger` with `args` in `cwd`, print what a modeler sees, and return it."""
    start = time.perf_counter()
    proc = subprocess.run(
        [str(DAGGER), *map(str, args)], cwd=cwd, env=CHILD_ENV, capture_output=True, text=True
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


print(f"workspace: {WORK}")
print(f"dagger: {DAGGER}")
print(f"python {sys.version.split()[0]} at {sys.executable}; commit {commit()}")

# %% [markdown]
# ## C1 — One definition declares the steps; custom steps are first-class (local)
#
# The definition below has python steps, a fan-out over a two-location handoff, a
# notebook step, and one hand-built command step, all wired by artifact key through one
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
# `dagger --help` lists the verbs. There is no `vwf` script, and a preview of a definition
# does not import Jobmon.

# %%
dagger("--help", cwd=GOOD)
assert not (DAGGER.parent / "vwf").exists()
probe = (
    "import sys; from click.testing import CliRunner; from vivarium.dagger.cli import dagger; "
    "r = CliRunner().invoke(dagger, ['preview', '-c', 'pipeline.py:stable']); "
    "print('exit', r.exit_code, 'jobmon imported:', any(m.split('.')[0] == 'jobmon' for m in sys.modules))"
)
out = subprocess.run(
    [sys.executable, "-c", probe], cwd=GOOD, env=CHILD_ENV, capture_output=True, text=True
)
print(out.stdout.strip())
assert "exit 0 jobmon imported: False" in out.stdout

# %% [markdown]
# ## C3 — Exit status: 0 success, 1 a failed step, 2 a wrong definition or command (local)
#
# A definition runs only with `--local` until MIC-7752 routes it through Jobmon; without
# it, `dagger` says so and exits 2. A failed step exits 1, so the status can gate the next
# command in a shell. The failing definition here is C9's.

# %%
no_local = dagger("run", "-c", "pipeline.py:stable", cwd=GOOD)
assert no_local.returncode == 2 and "--local" in no_local.stderr

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
# same, runs adults alone. `pipeline.py:stamped` has a population step that writes the
# time it ran, so forcing it changes population.parquet: adults runs again, but adults
# drops that column, so its output is unchanged and summary and later steps stay skipped.

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
changed = dagger("force", "-c", "pipeline.py:stamped", "--local", "population", cwd=FORCE)
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
# The source notebook is unchanged. A changed parameter marks the step in preview.

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
# ## C8, C9, C13 — Plain errors (local)
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
    proc = dagger(*args, cwd=folder, stdout=False)
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
    edits=[('run=f"mkdir -p', 'run=f"false && mkdir -p')],
    args=("run", "-c", "pipeline.py:stable", "--local"),
    exit_code=1,
    names=["archive", "status 1"],
    absent=["mkdir -p"],
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
show_error(
    "C1 a definition run without --local",
    args=("run", "-c", "pipeline.py:stable"),
    exit_code=2,
    names=["--local"],
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
# hand-built command step writes them into `nigeria.hdf` as `make_artifacts` would; and a
# notebook step runs the model's `hemorrhage_split_proportions.ipynb` V&V notebook on that
# artifact in the model's artifact env.
#
# It needs the GBD database, `/snfs1`, and two venvs over the Jenkins
# `vivarium_gates_mncnh_artifact_current` shared env in an MNCNH checkout:
# `.venv/vivarium_gates_mncnh_artifact` for the notebook, and the env running this
# walkthrough, which must import `vivarium_gates_mncnh` and this branch's dagger. Set
# `C_WALKTHROUGH_MNCNH` to the checkout and `DAGGER_SLICE_ROOT` to a new directory, and
# run on a compute node. The model's notebook has no `parameters` cell, so the demo copy
# adds one; that change is what a modeler makes to adopt `notebook_step` (MIC-7755).

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
                "".join(o.get("data", {}).get("text/plain", "") for o in cell["outputs"]),
            )

# %% [markdown]
# ## PLAY
#
# Try your own: change a step in `GOOD / "pipeline.py"`, run `preview` to see what would
# run, then `run --local --until <step>` or `force <step>`. `make_defs("mine", edits=[...])`
# gives a fresh copy with one-line edits applied.

# %%
# mine = make_defs("mine", edits=[('"count": [30, 55, 15]', '"count": [30, 60, 15]')])
# dagger("preview", "-c", "pipeline.py:stable", cwd=mine)
