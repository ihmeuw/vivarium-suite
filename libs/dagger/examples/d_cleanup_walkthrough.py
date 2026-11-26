# %% [markdown]
# # Track D walkthrough: the alpha's rules, `show`, `graph`, and what a definition may write
#
# This notebook is the evidence for the track D (docs and cleanup) validation review. Each
# section is headed by a claim from the review, with the ticket that made it true. Run it
# top to bottom: every outcome and timing below comes from your own run of the installed
# `dagger` command on this branch.
#
# Every section is local. No section uses Jobmon or the cluster, and every run passes
# `--local`. The whole notebook takes less than a minute on a laptop.
#
# Run it from `libs/dagger` with an env that has `vivarium-dagger` installed, as a notebook
# or with `python examples/d_cleanup_walkthrough.py`. It writes only under a workspace
# folder: `D_WALKTHROUGH_DIR` if that is set, and a new temporary folder if it is not. D3
# draws one SVG image if it finds Graphviz's `dot` program, and prints a note if it does
# not. Set `D_WALKTHROUGH_DOT` to the path of a `dot` program if none is on `PATH`.

# %% [markdown]
# ## How this notebook works
#
# The files a modeler writes are in `d_walkthrough_defs/`: the definition `pipeline.py`,
# the script `select_adults.py` that its command step runs, and the delivered file
# `inputs/census.csv`. The definition imports only `Pipeline` from
# `vivarium.dagger.frontend`. Every section runs the installed `dagger` command on that
# definition in a separate process, the way a modeler types it in a shell.
#
# The rest of this notebook is machinery for showing many runs and many mistakes in one
# place. A modeler would not write any of it.
#
# Each section works in its own copy of `d_walkthrough_defs/` under the workspace folder. A
# definition keeps its outputs next to itself (`HERE / "output"`), and its state inside
# them (`output/.dagger/walk`), so sections that shared one folder would each start from
# what the sections before left. The committed files never change.
#
# Several sections need a definition that differs from the committed one by a line or two.
# A modeler would edit their file. Here `make_defs(name, edits=[(old, new)])` makes the
# copy, applies each edit and prints it as `- old` and `+ new`, so that you can see what
# changed before you read the message that it causes.
#
# `dagger(...)` runs the command, prints what a modeler would see in the terminal with its
# exit status and time, and returns the result so that the cell can check it. The other
# helpers read what a command printed or left on disk: `plan` reads the lines of a preview,
# `ran` reads the table that pytask prints for a run, `blocks` reads the output of
# `dagger show`, and `tree`, `snapshot` and `outputs` list files.
#
# The `assert` lines turn the walkthrough into a test of what each section claims. A
# modeler reads the output instead.
#
# `dagger` imports the definition file, so Python writes a `__pycache__` folder next to it.
# The file listings leave that folder out.
#
# D6 and D7 add steps with `Pipeline.add_task`, which takes a task built by hand. Building
# one needs two imports from the layer under the step methods,
# `vivarium.dagger.backend.task.Task` and `vivarium.dagger.backend.nodes.file_node`. Both
# are in the API reference, and the docstring of `add_task` names them, but they are not
# importable from `vivarium.dagger.frontend`. The edits in those sections print the import
# lines. The track D review document records this as a finding about the API: `add_task`
# is a method of the frontend's `Pipeline`, and its argument can be built only from
# backend modules.

# %% [markdown]
# This cell imports what the notebook needs and finds this example folder, the library,
# the installed `dagger` next to this Python, and the workspace. The child processes get
# this process's environment without its colour settings, which would otherwise put
# pytask's live tables into the captured output.

# %%
import hashlib
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

for EXAMPLES in (Path("examples").resolve(), Path.cwd()):
    if (EXAMPLES / "d_walkthrough_defs").is_dir():
        break
else:
    EXAMPLES = Path(__file__).resolve().parent
LIB = EXAMPLES.parent
DAGGER = Path(sys.executable).parent / "dagger"
WORK = Path(os.environ.get("D_WALKTHROUGH_DIR") or tempfile.mkdtemp(prefix="cleanup_"))
WORK = WORK.resolve()
WORK.mkdir(parents=True, exist_ok=True)
CHILD_ENV = {
    k: v
    for k, v in os.environ.items()
    if k not in {"FORCE_COLOR", "CLICOLOR_FORCE", "CLICOLOR"}
} | {"TERM": "dumb", "COLUMNS": "100"}
STEPS = ["population", "children", "adults", "summary"]
STARTED = time.perf_counter()


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
# These helpers copy the definition and apply the edits.

# %%
def lines(text, sign):
    """Return the lines of `text` without their common indentation, each after `sign`."""
    return [f"{sign} {line}" for line in textwrap.dedent(text).strip("\n").splitlines()]


def edit(path, pairs):
    """Apply (old, new) edits to the file at `path` and print each one; each old occurs once."""
    text = path.read_text()
    for old, new in pairs:
        assert text.count(old) == 1, f"{old!r} occurs {text.count(old)} times in {path.name}"
        text = text.replace(old, new)
        for number, line in enumerate(lines(old, "-") + lines(new, "+")):
            print(f"{path.name + ':' if number == 0 else '':<{len(path.name) + 1}} {line}")
    path.write_text(text)


def make_defs(name, edits=()):
    """Copy the definition folder into a new folder under WORK, apply the edits, and return it."""
    folder = WORK / name
    shutil.rmtree(folder, ignore_errors=True)
    shutil.copytree(
        EXAMPLES / "d_walkthrough_defs",
        folder,
        ignore=shutil.ignore_patterns("__pycache__", "output"),
    )
    edit(folder / "pipeline.py", edits)
    return folder


# The two edits that D6 and D7 share: the imports that a hand-built task needs, and the
# last line of pipeline(), before which the new steps are added.
TASK_IMPORTS = (
    "from vivarium.dagger.frontend import Pipeline\n",
    "from vivarium.dagger.backend.nodes import file_node\n"
    "from vivarium.dagger.backend.task import Task\n"
    "from vivarium.dagger.frontend import Pipeline\n",
)
LAST_LINE = "    return p\n"


# %% [markdown]
# These helpers run the installed `dagger` and read what it printed and left.

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
        report(
            args, proc.returncode, proc.seconds, proc.stdout if stdout else "", proc.stderr
        )
    return proc


def report(args, returncode, seconds, stdout, stderr):
    """Print a command, its exit status and time, and what it wrote."""
    print(f"$ dagger {shlex.join(map(str, args))}    # exit {returncode}, {seconds:.1f} s")
    if stdout.strip():
        print(stdout.rstrip())
    if stderr.strip():
        print("[stderr]\n" + stderr.rstrip())
    print()


def plan(proc):
    """Return the preview's run and skip lines as {step: line}, in the order printed."""
    lines = [l for l in proc.stdout.splitlines() if re.match(r"(run |skip) ", l)]
    return {l.split()[1].rstrip(":"): l for l in lines}


def ran(proc):
    """Return {step: outcome} from pytask's table for a run; '.' means the step succeeded."""
    rows = dict(re.findall(r"^│ (\S+)\s+│ (\S+)\s+│$", proc.stdout, flags=re.M))
    rows.pop("Task", None)
    return rows


def blocks(proc):
    """Return the blocks that `dagger show` printed as {step: text}, in the order printed."""
    found = {}
    for block in proc.stdout.strip().split("\n\n"):
        found[re.match(r"Step '([^']+)'", block).group(1)] = block
    return found


def tree(folder):
    """Return every path under `folder`, relative to it, without Python's bytecode caches."""
    return sorted(
        str(p.relative_to(folder)) + ("/" if p.is_dir() else "")
        for p in folder.rglob("*")
        if "__pycache__" not in p.parts
    )


def snapshot(folder):
    """Return {path: (size, SHA-256, time last written in ns)} for each file under `folder`."""
    return {
        str(p.relative_to(folder)): "folder"
        if p.is_dir()
        else (
            p.stat().st_size,
            hashlib.sha256(p.read_bytes()).hexdigest(),
            p.stat().st_mtime_ns,
        )
        for p in sorted(folder.rglob("*"))
    }


def edges(mermaid):
    """Return the edges of a Mermaid graph as a set of (from label, edge label, to label)."""
    nodes = dict(re.findall(r'^\s*(\w+)\[/?"([^"]+)"/?\]$', mermaid, flags=re.M))
    found = re.findall(r'^\s*(\w+) -->(?:\|"([^"]*)"\|)? (\w+)$', mermaid, flags=re.M)
    assert len(found) == mermaid.count("-->"), mermaid
    return {(nodes[start], label, nodes[end]) for start, label, end in found}


def outputs(folder):
    """Return {file name: time last written, in ns} for the files in the run's output folder."""
    out = folder / "output" / "walk"
    return (
        {p.name: p.stat().st_mtime_ns for p in sorted(out.glob("*"))} if out.exists() else {}
    )


# %% [markdown]
# The definition has two root inputs, the delivered `census.csv` and the script
# `select_adults.py`. The python step `population` reads the census, the python step
# `children` and the command step `adults` each read the population, and the python step
# `summary` reads the output of `adults`.

# %%
print((EXAMPLES / "d_walkthrough_defs" / "pipeline.py").read_text())

# %% [markdown]
# ## D1 — In this alpha every step runs on every run (local)
#
# MIC-7764. A step cannot yet declare a fingerprint of its code, so `dagger` counts every
# step as stale on every build. "What the alpha does" in `python_pipelines.rst` lists the
# consequences, and this section shows four of them.
#
# The same command, given twice, runs all four steps both times, and the second run writes
# every output file again. A preview after a run shows every step as `run`. `dagger force`
# on one step runs the same four steps as `dagger run`. `--until` is the only option that
# leaves steps out: in a fresh copy, `run --until adults` runs `population` and `adults`
# and writes only their files, and a preview with the same option shows the other two steps
# as `not selected`.
#
# `dagger restart` continues a YAML workflow from its results directory. It has no `-c`
# option, so it does not take a Python definition, and the last command of this section
# shows the refusal.

# %%
D1 = make_defs("d1")
first = dagger("run", "-c", "pipeline.py", "--local", cwd=D1)
written_first = outputs(D1)
second = dagger("run", "-c", "pipeline.py", "--local", cwd=D1, stdout=False)
written_second = outputs(D1)
print("first run: ", ran(first))
print("second run:", ran(second))
for run in (first, second):
    assert run.returncode == 0
    assert ran(run) == dict.fromkeys(STEPS, "."), ran(run)
assert sorted(written_first) == [
    "adults.csv",
    "children.csv",
    "population.csv",
    "summary.csv",
]
assert all(written_second[name] > written_first[name] for name in written_first)
print("the second run wrote every output again:", sorted(written_second))

# %%
after = dagger("preview", "-c", "pipeline.py", cwd=D1)
assert after.returncode == 0
assert list(plan(after)) == STEPS, plan(after)
assert all(line.startswith("run ") for line in plan(after).values())
assert "4 of 4 steps would run" in after.stdout

forced = dagger("force", "-c", "pipeline.py", "--local", "adults", cwd=D1, stdout=False)
print("force adults:", ran(forced))
assert forced.returncode == 0 and ran(forced) == ran(second)

# %%
D1_UNTIL = make_defs("d1_until")
until = dagger("run", "-c", "pipeline.py", "--local", "--until", "adults", cwd=D1_UNTIL)
print("ran:", ran(until), "outputs:", sorted(outputs(D1_UNTIL)))
assert until.returncode == 0
assert ran(until) == {"population": ".", "adults": "."}, ran(until)
assert sorted(outputs(D1_UNTIL)) == ["adults.csv", "population.csv"]
limited = dagger("preview", "-c", "pipeline.py", "--until", "adults", cwd=D1_UNTIL)
for step in ("children", "summary"):
    assert plan(limited)[step].startswith("skip ") and "not selected" in plan(limited)[step]
for step in ("population", "adults"):
    assert plan(limited)[step].startswith("run ")
assert "2 of 4 steps would run" in limited.stdout

restart = dagger("restart", "-c", "pipeline.py", cwd=D1_UNTIL)
assert restart.returncode == 2 and "No such option" in restart.stderr
assert "-c" in restart.stderr

# %% [markdown]
# ## D2 — `dagger show` prints what each step reads, writes and runs (local)
#
# MIC-7818. `dagger show` prints one block per step, in the order the definition adds the
# steps. pytask's table in D1 lists the steps by name, so the two orders differ for this
# definition. Each block has the step's name and kind, one line per input and output with
# its file key and path, and a `calls` line for a python step or a `runs` line with the
# whole shell command for a command step, in that order. The first line of a command
# step's block also gives its env. `dagger show` runs nothing and writes nothing,
# so the copy holds only the definition's own files afterward.
#
# With a step's name, it prints that step's block only. A name that is not a step is
# refused with exit status 2, and the message gives the closest name and the valid names.

# %%
D2 = make_defs("d2")
before = tree(D2)
shown = dagger("show", "-c", "pipeline.py", cwd=D2)
assert shown.returncode == 0 and shown.stderr == ""
assert list(blocks(shown)) == STEPS, list(blocks(shown))
population, adults = blocks(shown)["population"], blocks(shown)["adults"]
assert population.startswith("Step 'population' (python step)\n")
assert adults.startswith(f"Step 'adults' (command step, env {sys.prefix})\n")
layout = {
    step: [line.split()[0] for line in block.splitlines()[1:]]
    for step, block in blocks(shown).items()
}
assert layout["population"] == ["input", "output", "calls"], layout
assert layout["adults"][:4] == ["input", "input", "output", "runs"], layout
assert f"input  census: 'census.csv' at {D2 / 'inputs' / 'census.csv'}" in population
assert f"output 'population.csv' at {D2 / 'output' / 'walk' / 'population.csv'}" in population
assert "calls  pipeline:read_census" in population
assert f"input  script: 'select_adults.py' at {D2 / 'select_adults.py'}" in adults
assert f"output adults: 'adults.csv' at {D2 / 'output' / 'walk' / 'adults.csv'}" in adults
assert re.search(r"runs .*mkdir -p", adults) and f"python {D2 / 'select_adults.py'}" in adults
assert tree(D2) == before, tree(D2)

# %%
one = dagger("show", "-c", "pipeline.py", "adults", cwd=D2)
assert one.returncode == 0 and list(blocks(one)) == ["adults"]
assert blocks(one)["adults"] == adults
unknown = dagger("show", "-c", "pipeline.py", "adult", cwd=D2)
assert unknown.returncode == 2 and unknown.stdout == ""
assert "'adult'" in unknown.stderr and "Valid steps" in unknown.stderr
assert "Did you mean 'adults'" in unknown.stderr
assert all(f"'{step}'" in unknown.stderr for step in STEPS)
assert tree(D2) == before

# %% [markdown]
# ## D3 — `dagger graph` draws the steps, the root inputs read and the final outputs (local)
#
# MIC-7765. `dagger graph -c pipeline.py FILE` writes the graph to `FILE` and writes
# nothing else. The graph has a node for each step, a node for each root input that a step
# reads (`census.csv` and `select_adults.py`), and a node for each output that no step
# reads (`children.csv` and `summary.csv`). A file that one step writes and another reads,
# such as `population.csv`, is the label of the edge between the two steps. An edge from a
# root input or to a final output has no label, because the file's node already shows the
# key. The ticket's criteria asked for a label on the edges from root inputs, and #471
# records the difference as a decision.
#
# A root input that no step reads gets no node. The second graph below is drawn from a
# copy with one more declared root input, `unused.csv`, and it has the same nodes and
# edges as the first.
#
# The file's extension picks the format. `.mmd` is Mermaid text, `.md` is the same text in
# a Markdown code block, and `.dot` is Graphviz DOT text. An extension that is not a format
# is refused with exit status 2 and a message that names the formats.

# %%
D3 = make_defs("d3")
before = tree(D3)
drawn = dagger("graph", "-c", "pipeline.py", "graph.mmd", cwd=D3)
mermaid = (D3 / "graph.mmd").read_text()
print(mermaid)
assert drawn.returncode == 0 and drawn.stdout == "" and drawn.stderr == ""
nodes = dict(re.findall(r'^\s*(\w+)\[/?"([^"]+)"/?\]$', mermaid, flags=re.M))
assert sorted(nodes.values()) == sorted(
    ["population<br/>python step", "children<br/>python step", "adults<br/>command step"]
    + ["summary<br/>python step", "census.csv", "select_adults.py"]
    + ["children.csv", "summary.csv"]
), nodes
POPULATION, CHILDREN = "population<br/>python step", "children<br/>python step"
ADULTS, SUMMARY = "adults<br/>command step", "summary<br/>python step"
EDGES = {
    ("census.csv", "", POPULATION),
    ("select_adults.py", "", ADULTS),
    (POPULATION, "population.csv", CHILDREN),
    (POPULATION, "population.csv", ADULTS),
    (ADULTS, "adults.csv", SUMMARY),
    (CHILDREN, "", "children.csv"),
    (SUMMARY, "", "summary.csv"),
}
assert edges(mermaid) == EDGES, edges(mermaid)

D3_UNREAD = make_defs(
    "d3_unread",
    [
        (
            '"select_adults.py": HERE / "select_adults.py",\n',
            '"select_adults.py": HERE / "select_adults.py",\n'
            '            "unused.csv": HERE / "inputs" / "unused.csv",\n',
        )
    ],
)
unread = dagger("graph", "-c", "pipeline.py", "graph.mmd", cwd=D3_UNREAD)
assert unread.returncode == 0 and "unused" not in (D3_UNREAD / "graph.mmd").read_text()
assert (D3_UNREAD / "graph.mmd").read_text() == mermaid
print("The graph of the copy with the root input that no step reads is the same text.\n")

for name in ("graph.md", "graph.dot"):
    result = dagger("graph", "-c", "pipeline.py", name, cwd=D3)
    assert result.returncode == 0 and (D3 / name).stat().st_size > 0
markdown, dot_text = (D3 / "graph.md").read_text(), (D3 / "graph.dot").read_text()
assert markdown.startswith("```mermaid\n") and mermaid.strip() in markdown
assert dot_text.startswith("digraph") and 'label="census.csv"' in dot_text
assert set(tree(D3)) - set(before) == {"graph.mmd", "graph.md", "graph.dot"}, tree(D3)

refused = dagger("graph", "-c", "pipeline.py", "graph.txt", cwd=D3)
assert refused.returncode == 2 and not (D3 / "graph.txt").exists()
assert all(
    extension in refused.stderr for extension in (".txt", ".mmd", ".md", ".dot", ".svg")
)

# %% [markdown]
# `.svg`, `.png` and `.pdf` are images that Graphviz's `dot` program draws. Without `dot`
# on `PATH`, `dagger` refuses them with exit status 2, says what is missing, and names the
# text formats that need no other program. The first command below gets a `PATH` with
# every folder that holds a `dot` program taken out, so that it shows the refusal on a
# machine that has Graphviz too.
#
# The second command needs a `dot` program. The cell uses the program that
# `D_WALKTHROUGH_DOT` names, or the one on `PATH`. If neither exists, it prints a note and
# skips that command.

# %%
NO_DOT_PATH = os.pathsep.join(
    d for d in CHILD_ENV["PATH"].split(os.pathsep) if not (Path(d) / "dot").exists()
)
no_dot = dagger("graph", "-c", "pipeline.py", "graph.svg", cwd=D3, env={"PATH": NO_DOT_PATH})
assert no_dot.returncode == 2 and not (D3 / "graph.svg").exists()
assert all(word in no_dot.stderr for word in ("Graphviz", "dot", "PATH", ".mmd"))

DOT = next(
    (
        Path(candidate)
        for candidate in (
            os.environ.get("D_WALKTHROUGH_DOT"),
            shutil.which("dot", path=CHILD_ENV["PATH"]),
        )
        if candidate and Path(candidate).is_file()
    ),
    None,
)
if DOT is None:
    print(
        "No Graphviz dot program was found, so the SVG image was not drawn. "
        "Set D_WALKTHROUGH_DOT to the path of a dot program to draw it."
    )
else:
    with_dot = {"PATH": str(DOT.parent) + os.pathsep + NO_DOT_PATH}
    image = dagger("graph", "-c", "pipeline.py", "graph.svg", cwd=D3, env=with_dot)
    svg = (D3 / "graph.svg").read_text()
    print(f"graph.svg: {len(svg)} characters, drawn by {DOT}")
    assert image.returncode == 0 and "<svg" in svg and svg.rstrip().endswith("</svg>")
    assert all(label in svg for label in ("census.csv", "population", "summary.csv"))

# %% [markdown]
# ## D4 — Preview changes no file of a pipeline that has run (local)
#
# MIC-7802. After a run, `dagger preview` changes nothing under the output root, which for
# this definition also holds the state root (`output/.dagger/walk`). The cell records the
# path, size, SHA-256 hash and modification time of every file under `output/` after a
# run, runs two previews, and compares. The modification time is part of the comparison
# because the defect that MIC-7802 fixed was a preview that wrote pytask's
# `.pytask/file_hashes.json` again with the same bytes.

# %%
D4 = make_defs("d4")
assert dagger("run", "-c", "pipeline.py", "--local", cwd=D4, show=False).returncode == 0
recorded = snapshot(D4 / "output")
for args in (
    ("preview", "-c", "pipeline.py"),
    ("preview", "-c", "pipeline.py", "--until", "adults"),
):
    assert dagger(*args, cwd=D4).returncode == 0
    assert snapshot(D4 / "output") == recorded
files = [path for path, value in recorded.items() if value != "folder"]
print(f"{len(files)} files under output/ are unchanged after the previews:")
print("\n".join(f"  {path}" for path in files))
assert ".dagger/walk/.pytask/file_hashes.json" in files and "walk/summary.csv" in files

# %% [markdown]
# On a pipeline that never ran, the first preview does create something. As
# `docs/source/python_pipelines.rst` now says under "Previewing a run", it creates the
# state root, its `pytask.toml`, pytask's `.pytask` folder with pytask's database, and the
# run's empty output folder, and it writes no output file. The cell prints that sentence
# from the docs page of this checkout and then what the first preview left in a fresh copy.

# %%
page = " ".join((LIB / "docs" / "source" / "python_pipelines.rst").read_text().split())
sentence = next((s for s in page.split(". ") if "never ran" in s and "state root" in s), None)
assert (
    sentence
), 'python_pipelines.rst, "Previewing a run", has no sentence on a first preview'
print(sentence + ".\n")
assert all(name in sentence for name in ("state root", "pytask.toml", ".pytask", "database"))

D4_NEW = make_defs("d4_new")
before = tree(D4_NEW)
assert dagger("preview", "-c", "pipeline.py", cwd=D4_NEW, stdout=False).returncode == 0
created = sorted(set(tree(D4_NEW)) - set(before))
print("the first preview created:")
print("\n".join(f"  {path}" for path in created))
assert {
    "output/.dagger/walk/",
    "output/.dagger/walk/pytask.toml",
    "output/.dagger/walk/.pytask/",
    "output/.dagger/walk/.pytask/pytask.sqlite3",
    "output/walk/",
} <= set(created), created
assert all(
    path in ("output/", "output/.dagger/", "output/walk/")
    or path.startswith("output/.dagger/walk/")
    for path in created
), created
assert outputs(D4_NEW) == {}

# %% [markdown]
# ## D5 — An output must stay inside the run and out of other outputs (local)
#
# MIC-7809. A step's outputs go under the run's output directory, `output/walk` here. A
# step method refuses an output key that would land anywhere else, or inside another
# step's output, when the step is added. The definition then fails to load, so every
# command exits with status 2 before anything is written.
#
# Each case below changes one line of the definition: a key with `..`, an absolute path
# outside the run, and a key inside the output of `population`. The cell runs a preview
# and a local run on each, and checks the exit status, the words of the message, and that
# the folder holds the same files as before. The traceback ends on the line of the
# definition that adds the step.

# %%
def refuse_definition(name, edits, words, absent=()):
    """Make a copy with `edits`; check that preview and run exit 2 and write nothing."""
    folder = make_defs(name, edits)
    before = snapshot(folder / "inputs"), tree(folder)
    result = dagger("preview", "-c", "pipeline.py", cwd=folder)
    run = dagger("run", "-c", "pipeline.py", "--local", cwd=folder, show=False)
    print(
        f"run --local: exit {run.returncode}, the same message: {run.stderr == result.stderr}\n"
    )
    for proc in (result, run):
        assert proc.returncode == 2 and proc.stdout == "", (proc.returncode, proc.stdout)
        assert "Could not load the definition" in proc.stderr
        assert (
            f'{folder / "pipeline.py"}", line' in proc.stderr and "in pipeline" in proc.stderr
        )
        for word in words:
            assert word in proc.stderr, word
    assert (snapshot(folder / "inputs"), tree(folder)) == before, tree(folder)
    for path in absent:
        assert not Path(path).exists(), path
    return folder


OUTSIDE = WORK / "outside" / "escaped.csv"
refuse_definition(
    "d5_dotdot",
    [('outputs="children.csv"', 'outputs="../escaped.csv"')],
    words=["Step 'children'", "'../escaped.csv'", "not inside the run's output directory"],
)
refuse_definition(
    "d5_absolute",
    [('outputs="children.csv"', f'outputs="{OUTSIDE}"')],
    words=["Step 'children'", str(OUTSIDE), "not inside the run's output directory"],
    absent=[OUTSIDE, OUTSIDE.parent],
)
refuse_definition(
    "d5_nested",
    [('outputs={"adults": "adults.csv"}', 'outputs={"adults": "population.csv/adults.csv"}')],
    words=[
        "Step 'adults'",
        "'population.csv/adults.csv'",
        "is inside output 'population.csv'",
    ],
)

# %% [markdown]
# ## D6 — No output may be a root input or a folder that holds one (local)
#
# MIC-7853. A root input is a file that the pipeline reads and no step produces, so no
# step's declared output may overwrite or replace one. The `Pipeline` refuses such an
# output when the step is added, for a step method and for `Pipeline.add_task` alike.
#
# The first three cases use the step methods. The first gives the python step `children`
# the output key `census.csv`, which is the key of a root input. The step methods refused
# that key before MIC-7853 too, so the case is here for completeness. The next two cases
# are the ones that the step methods accepted before MIC-7853, and each declares one more
# root input that lies inside the run's output directory. In the second case the root
# input `last_year` is the file `output/walk/children.csv`, which is where the step
# `children` writes, and the message names both keys. In the third case the root input
# `seed.csv` is in the folder `output/walk/adults`, and the command step's output is that
# folder.
#
# The last two cases add a hand-built command task with `add_task`: one whose output is
# the delivered file `inputs/census.csv`, and one whose output is the folder `inputs` that
# holds it. A hand-built task names its outputs by path, and the message names the folder
# by path for that reason.
#
# Each case exits with status 2 and writes nothing, and `inputs/census.csv` has the same
# bytes afterward.

# %%
refuse_definition(
    "d6_step_method",
    [('outputs="children.csv"', 'outputs="census.csv"')],
    words=["Step 'children'", "'census.csv'", "root input", "cannot also produce it"],
)
LAST_ROOT_INPUT = '"select_adults.py": HERE / "select_adults.py",\n'
refuse_definition(
    "d6_other_key",
    [
        (
            LAST_ROOT_INPUT,
            LAST_ROOT_INPUT
            + '            "last_year": HERE / "output" / "walk" / "children.csv",\n',
        )
    ],
    words=[
        "Step 'children'",
        "'children.csv'",
        "is the declared root input 'last_year'",
        "cannot also produce it",
    ],
)
refuse_definition(
    "d6_folder",
    [
        (
            LAST_ROOT_INPUT,
            LAST_ROOT_INPUT
            + '            "seed.csv": HERE / "output" / "walk" / "adults" / "seed.csv",\n',
        ),
        ('outputs={"adults": "adults.csv"}', 'outputs={"adults": "adults"}'),
    ],
    words=["Step 'adults'", "holds the declared root input 'seed.csv'", "cannot hold a root"],
)
STAMP = """\
    census = HERE / "inputs" / "census.csv"
    stamp = Task(
        name="stamp",
        run=f"date > {census}",
        inputs={},
        outputs={"census": file_node(census)},
        resources={},
        env=None,
    )
    p.add_task(stamp)
"""
CLEAR = """\
    inputs = HERE / "inputs"
    clear = Task(
        name="clear",
        run=f"rm -r {inputs} && mkdir {inputs}",
        inputs={},
        outputs={"inputs": file_node(inputs)},
        resources={},
        env=None,
    )
    p.add_task(clear)
"""
refuse_definition(
    "d6_add_task_file",
    [TASK_IMPORTS, (LAST_LINE, STAMP + LAST_LINE)],
    words=["Step 'stamp'", "'census.csv'", "root input", "cannot also produce it"],
)
D6 = refuse_definition(
    "d6_add_task_folder",
    [TASK_IMPORTS, (LAST_LINE, CLEAR + LAST_LINE)],
    words=[
        "Step 'clear'",
        "holds the declared root input 'census.csv'",
        "cannot hold a root",
    ],
)
committed = EXAMPLES / "d_walkthrough_defs" / "inputs" / "census.csv"
assert (D6 / "inputs" / "census.csv").read_bytes() == committed.read_bytes()

# %% [markdown]
# ## D7 — A step before its producer, and a cycle, are refused by preview, show and graph alike (local)
#
# MIC-7848, #489. A step method refuses an input key that no earlier step produces, so a
# definition written with the step methods and file keys cannot put a consumer first.
# `add_task` does not check this when the task is added. `dagger preview` checks it when
# it builds the pipeline, and since #489 `dagger show` and `dagger graph` check it too, so
# that they do not print or draw a definition that a preview refuses.
#
# The first definition adds the hand-built task `copy_notes`, which reads `notes.txt`,
# before `write_notes`, which writes it. Each of the three commands exits with status 2
# and the same message, which names both steps and says which to add first. No command
# writes a file.
#
# The second definition is the first with one more edit: `write_notes` now reads
# `copy.txt`, so the two tasks read each other's output. Each command exits
# with status 2 and pytask's description of the cycle, which names both steps. `show` and
# `graph` write nothing, and `graph` writes no graph file. Preview finds the cycle when
# pytask builds the graph, after it has created the state root, so it leaves the files
# that a first preview creates (D4) and its message starts with `Preview could not build
# the plan`.
#
# The third definition has both mistakes: the first definition's two tasks, and two more
# tasks, `ping` and `pong`, that read each other's output. When a definition has an order
# error and a cycle, each of the three commands reports the order error and does not
# mention the cycle, as a run does. The three messages are the same, and no command writes
# a file.

# %%
CONSUMER_FIRST = """\
    notes = HERE / "output" / "walk" / "notes.txt"
    copy = HERE / "output" / "walk" / "copy.txt"
    copy_notes = Task(
        name="copy_notes",
        run=f"cp {notes} {copy}",
        inputs={"notes": file_node(notes)},
        outputs={"copy": file_node(copy)},
        resources={},
        env=None,
    )
    write_notes = Task(
        name="write_notes",
        run=f"date > {notes}",
        inputs={},
        outputs={"notes": file_node(notes)},
        resources={},
        env=None,
    )
    p.add_task(copy_notes)
    p.add_task(write_notes)
"""
READS_THE_COPY = (
    'run=f"date > {notes}",\n        inputs={},\n',
    'run=f"cp {copy} {notes}",\n        inputs={"copy": file_node(copy)},\n',
)
PING_PONG = """\
    ping_file = HERE / "output" / "walk" / "ping.txt"
    pong_file = HERE / "output" / "walk" / "pong.txt"
    ping = Task(
        name="ping",
        run=f"cp {pong_file} {ping_file}",
        inputs={"pong": file_node(pong_file)},
        outputs={"ping": file_node(ping_file)},
        resources={},
        env=None,
    )
    pong = Task(
        name="pong",
        run=f"cp {ping_file} {pong_file}",
        inputs={"ping": file_node(ping_file)},
        outputs={"pong": file_node(pong_file)},
        resources={},
        env=None,
    )
    p.add_task(ping)
    p.add_task(pong)
"""
ORDER = make_defs("d7_order", [TASK_IMPORTS, (LAST_LINE, CONSUMER_FIRST + LAST_LINE)])
print()
CYCLE = make_defs(
    "d7_cycle", [TASK_IMPORTS, (LAST_LINE, CONSUMER_FIRST + LAST_LINE), READS_THE_COPY]
)
print()
BOTH = make_defs(
    "d7_both", [TASK_IMPORTS, (LAST_LINE, CONSUMER_FIRST + PING_PONG + LAST_LINE)]
)
print()
VERBS = {
    "show": ("show", "-c", "pipeline.py"),
    "graph": ("graph", "-c", "pipeline.py", "graph.mmd"),
    "preview": ("preview", "-c", "pipeline.py"),
}
refusals, left = {}, {}
for folder in (ORDER, CYCLE, BOTH):
    before = tree(folder)
    for verb, args in VERBS.items():
        result = dagger(*args, cwd=folder)
        refusals[folder.name, verb] = result
        left[folder.name, verb] = sorted(set(tree(folder)) - set(before))
        assert result.returncode == 2 and result.stdout == "", (verb, result.returncode)
        assert "'copy_notes'" in result.stderr or "copy_notes\n" in result.stderr
        assert "write_notes" in result.stderr
        assert not (folder / "graph.mmd").exists()

for name in ("d7_order", "d7_both"):
    order_messages = {refusals[name, verb].stderr for verb in VERBS}
    assert len(order_messages) == 1, order_messages
    message = order_messages.pop()
    assert "add step 'write_notes' before step 'copy_notes'" in message
    assert "cycle" not in message and "ping" not in message, message
    assert all(left[name, verb] == [] for verb in VERBS), left
for verb in VERBS:
    assert "The DAG contains cycles" in refusals["d7_cycle", verb].stderr, verb
assert "Preview could not build the plan" in refusals["d7_cycle", "preview"].stderr
assert refusals["d7_cycle", "show"].stderr == refusals["d7_cycle", "graph"].stderr
assert left["d7_cycle", "show"] == [] and left["d7_cycle", "graph"] == []
print("the preview of the cycle left:", left["d7_cycle", "preview"])
assert outputs(CYCLE) == {}

# %% [markdown]
# ## D8 — An interrupted local run exits 130, and the next run runs every step again (local)
#
# MIC-7764, MIC-7800. One edit makes the command step sleep for five seconds before its
# script runs. The cell starts `dagger run --local`, waits until `population` has written
# its file, and then sends SIGINT to the run's process group, which is what the terminal
# does when you press Ctrl-C. `dagger` exits with status 130 and says on standard error
# `Interrupted. Run the same command again to run the pipeline again.` The step that was
# interrupted and the step after it wrote no file.
#
# The same command, given again, runs all four steps, including `population`, which had
# finished before the interrupt. Its file is written again.
#
# This section covers a local run only. The cluster side of an interrupted run (the margin
# before the allocation ends, the stop at half the margin, and SIGTERM and SIGHUP) is in
# the review document.

# %%
D8 = make_defs(
    "d8",
    [
        (
            '"python {script} {population} {adults}"',
            '"sleep 5 && python {script} {population} {adults}"',
        )
    ],
)
args = ("run", "-c", "pipeline.py", "--local")
start = time.perf_counter()
running = subprocess.Popen(
    [str(DAGGER), *args],
    cwd=D8,
    env=CHILD_ENV,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    start_new_session=True,
)
deadline = time.monotonic() + 30
while (
    "population.csv" not in outputs(D8)
    and running.poll() is None
    and time.monotonic() < deadline
):
    time.sleep(0.05)
if running.poll() is not None or "population.csv" not in outputs(D8):
    running.kill()
    raise AssertionError(("the run did not reach the sleeping step", running.communicate()))
time.sleep(1)
os.killpg(running.pid, signal.SIGINT)
out, err = running.communicate(timeout=60)
report(args, running.returncode, time.perf_counter() - start, out, err)
interrupted = outputs(D8)
print("outputs after the interrupt:", sorted(interrupted))
assert running.returncode == 130, running.returncode
assert "Interrupted." in err and "Run the same command again" in err
assert "to run the pipeline again" in err and "resume" not in err.lower()
assert "population.csv" in interrupted
assert "adults.csv" not in interrupted and "summary.csv" not in interrupted

# %%
again = dagger(*args, cwd=D8)
assert again.returncode == 0
assert ran(again) == dict.fromkeys(STEPS, "."), ran(again)
assert sorted(outputs(D8)) == ["adults.csv", "children.csv", "population.csv", "summary.csv"]
assert outputs(D8)["population.csv"] > interrupted["population.csv"]
print("population.csv was written again by the next run.")

# %%
print(
    f"All sections passed in {time.perf_counter() - STARTED:.0f} s. The workspace is {WORK}."
)

# %% [markdown]
# ## PLAY
#
# Try your own. `make_defs("mine", edits=[...])` gives a fresh copy of the definition with
# your edits applied, and `dagger(..., cwd=mine)` runs a command in it. Remove the `#`
# from the lines below, change the edit or the command, and run the cell.

# %%
# mine = make_defs("mine", edits=[('outputs="children.csv"', 'outputs="young/children.csv"')])
# dagger("show", "-c", "pipeline.py", "children", cwd=mine)
# dagger("graph", "-c", "pipeline.py", "graph.mmd", cwd=mine)
# print((mine / "graph.mmd").read_text())
