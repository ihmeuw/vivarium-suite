"""A stub ``psimulate`` in a fake environment, and the toy inputs it reads, for simulation
step tests.

No test runs the real ``psimulate run``. :func:`make_stub_env` makes an environment
prefix whose ``bin/psimulate`` is a Python script that records its arguments and writes
psimulate's layout for a run. A step whose ``env`` is that prefix finds the stub first
on ``PATH``, ahead of any real ``psimulate`` installed in the test environment. The stub
reads the knobs below from its environment, which a build passes to the step's command
and :class:`~vivarium.dagger.harness.FakeJobmon` passes to its children, so a test sets
them with ``monkeypatch.setenv``.

The stub parses ``run -M <spec> -B <branches> [-i <artifact>] -o <dir>`` and any other
options, appends its arguments to the log, reads ``input_draw_count`` and
``random_seed_count`` from the branches file, and writes one run to
``<dir>/<model name>/<timestamp>/``: ``metadata/<task id>.json`` for every simulation,
``results/dead/<task id>.parquet`` with the columns ``input_draw``, ``random_seed`` and
``value`` for every finished simulation, and an empty ``logs/``. The model name follows
psimulate's rule: the stem of ``-i`` when given, else the stem of the spec's
``configuration.input_data.artifact_path`` when it sets one, else the spec's stem.
"""

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from vivarium.dagger.frontend import Pipeline

LOG_VARIABLE = "STUB_PSIMULATE_LOG"
"""The file the stub appends its arguments to, as one JSON list per call. Unset, the
stub logs nothing."""

UNFINISHED_VARIABLE = "STUB_PSIMULATE_UNFINISHED"
"""How many simulations, the last ones in task id order, get no results file in any
metric. Unset means 0."""

PARTIAL_METRIC_VARIABLE = "STUB_PSIMULATE_PARTIAL_METRIC"
"""Set to ``1`` to also write the metric :data:`SECOND_METRIC` and leave one
simulation's file out of it, so that simulation is unfinished in one metric only."""

NO_METADATA_VARIABLE = "STUB_PSIMULATE_NO_METADATA"
"""Set to ``1`` to write no ``metadata/`` files, as a psimulate before
vivarium-cluster-tools 3.0.0 does."""

RUN_DIRECTORIES_VARIABLE = "STUB_PSIMULATE_RUN_DIRECTORIES"
"""How many run directories the stub writes under ``-o``: ``0``, ``1`` or ``2``. Unset
means 1. The second has the same model name and a later timestamp."""

EXIT_VARIABLE = "STUB_PSIMULATE_EXIT"
"""An exit status the stub exits with after it logs its arguments and before it writes
anything under ``-o``. Unset, the stub writes the run and exits 0."""

KNOB_VARIABLES = (
    UNFINISHED_VARIABLE,
    PARTIAL_METRIC_VARIABLE,
    NO_METADATA_VARIABLE,
    RUN_DIRECTORIES_VARIABLE,
    EXIT_VARIABLE,
)
"""Every knob that changes what the stub writes, which a test unsets before it starts."""

METRIC = "dead"
"""The metric directory the stub always writes, as psimulate's deaths observer names it."""

SECOND_METRIC = "ylls"
"""The second metric directory the stub writes when :data:`PARTIAL_METRIC_VARIABLE` is set."""

EXAMPLE_DIR = Path(__file__).resolve().parent / "simulation_example"
"""The example definition's directory, with its toy spec and branches files."""

COMMAND_FRAGMENTS = ("psimulate run", "python -I", "mkdir", "rm -rf", "CalledProcessError")
"""Text from the generated command or its error that a step's failure line must not hold."""

NO_PREVIOUS = "There was no previous run."
"""The sentence that ends a failure message when the run directory held no complete run."""

_ENV_LINE = "ENV = sys.prefix"
"""The line of the example definition that names the environment psimulate runs in."""

# The stub's own source. __PYTHON__ is replaced by sys.executable, so the stub imports
# pandas and yaml from the test environment. Its value per simulation is stub_deaths's.
_STUB_SOURCE = r'''#!__PYTHON__
"""Stand in for psimulate run: record the arguments and write psimulate's layout."""

import datetime
import json
import os
import sys
from pathlib import Path

import pandas as pd
import yaml


def _value(args, flag):
    return args[args.index(flag) + 1] if flag in args else None


def _model_name(spec, artifact):
    if artifact is not None:
        return Path(artifact).stem
    loaded = yaml.safe_load(Path(spec).read_text()) or {}
    named = ((loaded.get("configuration") or {}).get("input_data") or {}).get(
        "artifact_path"
    )
    return Path(named).stem if named else Path(spec).stem


def _write_run(run, simulations, finished):
    partial = os.environ.get("STUB_PSIMULATE_PARTIAL_METRIC") == "1"
    metadata = os.environ.get("STUB_PSIMULATE_NO_METADATA") != "1"
    metrics = ["dead", "ylls"] if partial else ["dead"]
    (run / "logs").mkdir(parents=True)
    for metric in metrics:
        (run / "results" / metric).mkdir(parents=True)
    if metadata:
        (run / "metadata").mkdir()
    for index, (draw, seed) in enumerate(simulations):
        task_id = f"sim_{index:03d}"
        if metadata:
            record = {"input_draw": draw, "random_seed": seed}
            (run / "metadata" / f"{task_id}.json").write_text(json.dumps(record))
        if index >= finished:
            continue
        frame = pd.DataFrame(
            {"input_draw": [draw], "random_seed": [seed], "value": [1 + 10 * draw + seed]}
        )
        frame.to_parquet(run / "results" / "dead" / f"{task_id}.parquet")
        if partial and index != finished - 1:
            frame.to_parquet(run / "results" / "ylls" / f"{task_id}.parquet")


def main():
    args = sys.argv[1:]
    log = os.environ.get("STUB_PSIMULATE_LOG")
    if log:
        with open(log, "a") as stream:
            stream.write(json.dumps(args) + "\n")
    if not args or args[0] != "run" or not all(f in args for f in ("-M", "-B", "-o")):
        print(f"stub psimulate: unexpected arguments {args}", file=sys.stderr)
        return 2
    status = os.environ.get("STUB_PSIMULATE_EXIT")
    if status:
        return int(status)
    keyspace = yaml.safe_load(Path(_value(args, "-B")).read_text()) or {}
    draws = int(keyspace.get("input_draw_count", 1))
    seeds = int(keyspace.get("random_seed_count", 1))
    simulations = [(draw, seed) for draw in range(draws) for seed in range(seeds)]
    finished = len(simulations) - int(os.environ.get("STUB_PSIMULATE_UNFINISHED") or 0)
    model = _model_name(_value(args, "-M"), _value(args, "-i"))
    start = datetime.datetime.now()
    for index in range(int(os.environ.get("STUB_PSIMULATE_RUN_DIRECTORIES") or 1)):
        stamp = (start + datetime.timedelta(seconds=index)).strftime("%Y_%m_%d_%H_%M_%S")
        _write_run(Path(_value(args, "-o")) / model / stamp, simulations, finished)
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def make_stub_env(prefix: Path) -> Path:
    """Create a fake env at ``prefix`` whose ``bin/psimulate`` is the stub; return ``prefix``.

    ``bin/python`` is a symlink to ``sys.executable``, so the env resolves and the step's
    check runs on the test interpreter, and ``bin/psimulate`` has a ``#!<sys.executable>``
    shebang. ``prefix`` may contain a space or a quote.
    """
    bin_dir = prefix / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").symlink_to(sys.executable)
    stub = bin_dir / "psimulate"
    stub.write_text(_STUB_SOURCE.replace("__PYTHON__", sys.executable, 1))
    stub.chmod(0o755)
    return prefix


def read_argv_log(log: Path) -> list[list[str]]:
    """Return the arguments of each call the stub logged to ``log``, in call order."""
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


def write_spec(path: Path, artifact_path: str | None = None) -> Path:
    """Write a toy model spec to ``path``, naming ``artifact_path`` when given; return ``path``."""
    spec: dict[str, Any] = yaml.safe_load((EXAMPLE_DIR / "toy_model.yaml").read_text())
    if artifact_path is not None:
        spec["configuration"]["input_data"] = {"artifact_path": artifact_path}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(spec))
    return path


def write_branches(path: Path, draws: int = 2, seeds: int = 2) -> Path:
    """Write a branches file with ``draws`` input draws and ``seeds`` random seeds; return ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"input_draw_count": draws, "random_seed_count": seeds}))
    return path


def copy_simulation_example(destination: Path, env: Path) -> Path:
    """Copy :data:`EXAMPLE_DIR` to ``destination`` with its env set to ``env``; return the definition.

    The copy's one ``ENV = sys.prefix`` line is replaced by ``env``'s path, and the
    function asserts that it replaced exactly one line.
    """
    ignored = shutil.ignore_patterns("__pycache__", "output", "state")
    shutil.copytree(EXAMPLE_DIR, destination, ignore=ignored)
    definition = destination / "pipeline.py"
    lines = definition.read_text().splitlines(keepends=True)
    matches = [index for index, line in enumerate(lines) if line.rstrip("\n") == _ENV_LINE]
    assert len(matches) == 1, f"expected one {_ENV_LINE!r} line in {definition}: {matches}"
    lines[matches[0]] = f"ENV = {str(env)!r}\n"
    definition.write_text("".join(lines))
    return definition


def stub_deaths(draw: int, seed: int) -> int:
    """Return the ``value`` the stub writes for the simulation of ``draw`` and ``seed``."""
    return 1 + 10 * draw + seed


def deaths_by_draw(draws: int = 2, seeds: int = 2) -> dict[int, int]:
    """Return the stub's ``value`` summed over seeds for each input draw of a complete run."""
    return {
        draw: sum(stub_deaths(draw, seed) for seed in range(seeds)) for draw in range(draws)
    }


def summarize_run(simulation: Path) -> pd.DataFrame:
    """Sum the stub's deaths by input draw, and record what the step received.

    Each row also holds ``str(simulation)`` as ``received``, whether ``simulation`` is a
    ``Path`` as ``is_path``, and how many parquet files the metric directory holds as
    ``files``, so a test can check the argument a consumer step was given.
    """
    files = sorted((simulation / "results" / METRIC).glob("*.parquet"))
    frame = pd.concat([pd.read_parquet(path) for path in files], ignore_index=True)
    summary = frame.groupby("input_draw")["value"].sum().reset_index()
    summary["received"] = str(simulation)
    summary["is_path"] = isinstance(simulation, Path)
    summary["files"] = len(files)
    return summary


def summary_values(summary: pd.DataFrame) -> dict[int, int]:
    """Return a summary's ``value`` by ``input_draw``, as plain ints."""
    return {
        int(draw): int(value) for draw, value in zip(summary["input_draw"], summary["value"])
    }


def scratch_for(run_directory: Path) -> Path:
    """Return the scratch directory psimulate writes to for ``run_directory``."""
    return run_directory.with_name(f"{run_directory.name}.psimulate")


def snapshot(directory: Path) -> dict[str, bytes]:
    """Return the bytes of every file under ``directory``, keyed by its relative path."""
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def assert_no_command(text: str, pipeline: Pipeline, step: str) -> None:
    """Assert that ``text`` holds neither the command of ``pipeline``'s ``step`` nor a part of it."""
    [task] = [task for task in pipeline.tasks if task.name == step]
    assert isinstance(task.run, str), task.run
    assert task.run not in text
    for fragment in COMMAND_FRAGMENTS:
        assert fragment not in text, (fragment, text)
