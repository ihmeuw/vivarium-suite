"""Steps, pipelines and helpers for the track B (Jobmon bridge) walkthrough.

A bridged build sends each Python step to a node by its import path, so the step
functions live in this importable module rather than in the notebook. Each step returns
its upstream's rows plus one row of its own that records where and when it ran: the
host, the process id, the interpreter, and the start and end times.

The module also builds the walkthrough's pipelines, so that a build started outside the
notebook (on another node, or as a separate controller job) makes the same tasks. Call
:func:`main` with ``["rebuild", base]`` or ``["controller", base, cluster, pause]`` for
those, from a process that imports this module: run as a script, the module would be
``__main__``, and a node could not import its step functions.
"""

import json
import os
import socket
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from vivarium.dagger.backend.build import build, outcomes
from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import ArtifactRegistry
from vivarium.dagger.bridge.execute import RemoteTaskFailed
from vivarium.dagger.bridge.state import RUN_STATE_KEY
from vivarium.dagger.frontend.python_step import python_step
from vivarium.dagger.harness import stable_code_id

SLURM_RESOURCES = {
    "project": "proj_simscience",
    "queue": "all.q",
    "memory": 1,
    "runtime": 600,
    "cores": 1,
}
"""The compute resources every cluster step starts from."""


# Steps


def _row(step: str, started: float) -> dict[str, object]:
    """Return one row recording where and when ``step`` ran."""
    return {
        "step": step,
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "python": sys.executable,
        "started": started,
        "finished": time.time(),
    }


def _with_row(upstream: Path | None, step: str, started: float) -> pd.DataFrame:
    """Return the upstream product's rows with this step's row appended."""
    rows = [] if upstream is None else pd.read_parquet(upstream).to_dict("records")
    return pd.DataFrame([*rows, _row(step, started)])


def start(seed: Path) -> pd.DataFrame:
    """Begin a chain: one row for this step."""
    started = time.time()
    seed.read_text()
    return _with_row(None, "start", started)


def extend(upstream: Path) -> pd.DataFrame:
    """Add one row to the upstream chain."""
    return _with_row(upstream, "extend", time.time())


def extend_unless_switched(upstream: Path, switch: Path) -> pd.DataFrame:
    """Add one row, or raise when the switch file says ``fail``."""
    started = time.time()
    if switch.read_text().strip() == "fail":
        raise RuntimeError(f"The switch at {switch} says 'fail'.")
    return _with_row(upstream, "extend_unless_switched", started)


def extend_after_a_pause(upstream: Path, pause: Path) -> pd.DataFrame:
    """Wait the number of seconds in the pause file, then add one row."""
    started = time.time()
    time.sleep(float(pause.read_text()))
    return _with_row(upstream, "extend_after_a_pause", started)


def extend_holding_memory(upstream: Path, megabytes: Path) -> pd.DataFrame:
    """Touch the number of megabytes in the file, so a small memory limit kills the step."""
    started = time.time()
    block = np.ones(int(megabytes.read_text()) * 1024 * 1024 // 8)
    block.sum()
    return _with_row(upstream, "extend_holding_memory", started)


def doubled(numbers: Path) -> pd.DataFrame:
    """Return the numbers in the CSV file, each doubled; the same wherever it runs."""
    frame = pd.read_csv(numbers)
    return frame.assign(n=frame["n"] * 2)


# Pipelines


def stable(task: Task, version: str = "1") -> Task:
    """Give ``task`` a stable code identity, so it skips when nothing it reads changed."""
    return replace(task, code_id=stable_code_id(f"{task.name}::code", version))


def _base(base: Path, **files: str) -> ArtifactRegistry:
    """Write each root input file under ``base`` and return a registry over them."""
    base.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        path = base / f"{name}.txt"
        if not path.exists():
            path.write_text(f"{text}\n")
    return ArtifactRegistry(
        base / "out", "run1", root_inputs={name: base / f"{name}.txt" for name in files}
    )


def chain(base: Path, *, switch: str = "ok") -> tuple[list[Task], ArtifactRegistry]:
    """Return the chain a -> b -> c plus the independent d, with stable code identities.

    a and d read the root input ``seed``; b raises while the root input ``switch`` says
    ``fail``. The switch file is written only when it is missing.
    """
    registry = _base(base, seed="seed", switch=switch)
    tasks = [
        python_step(
            start, inputs={"seed": "seed"}, produces="a.parquet", registry=registry, name="a"
        ),
        python_step(
            extend_unless_switched,
            inputs={"upstream": "a.parquet", "switch": "switch"},
            produces="b.parquet",
            registry=registry,
            name="b",
        ),
        python_step(
            extend,
            inputs={"upstream": "b.parquet"},
            produces="c.parquet",
            registry=registry,
            name="c",
        ),
        python_step(
            start, inputs={"seed": "seed"}, produces="d.parquet", registry=registry, name="d"
        ),
    ]
    return [stable(task) for task in tasks], registry


def slow_chain(base: Path, pause: float) -> tuple[list[Task], ArtifactRegistry]:
    """Return a -> b -> c, where b and c each wait ``pause`` seconds before they finish."""
    registry = _base(base, seed="seed", pause=str(pause))
    tasks = [
        python_step(
            start, inputs={"seed": "seed"}, produces="a.parquet", registry=registry, name="a"
        ),
        python_step(
            extend_after_a_pause,
            inputs={"upstream": "a.parquet", "pause": "pause"},
            produces="b.parquet",
            registry=registry,
            name="b",
        ),
        python_step(
            extend_after_a_pause,
            inputs={"upstream": "b.parquet", "pause": "pause"},
            produces="c.parquet",
            registry=registry,
            name="c",
        ),
    ]
    return [stable(task) for task in tasks], registry


def doubling(base: Path) -> tuple[list[Task], ArtifactRegistry]:
    """Return one Python step that doubles the numbers in ``base / "numbers.csv"``."""
    base.mkdir(parents=True, exist_ok=True)
    numbers = base / "numbers.csv"
    if not numbers.exists():
        pd.DataFrame({"n": [1, 2, 3]}).to_csv(numbers, index=False)
    registry = ArtifactRegistry(base / "out", "run1", root_inputs={"numbers": numbers})
    task = python_step(
        doubled,
        inputs={"numbers": "numbers"},
        produces="doubled.parquet",
        registry=registry,
        name="doubled",
    )
    return [stable(task)], registry


# Reading results


def outcome_names(session: Any) -> dict[str, str]:
    """Return each step's outcome by name."""
    return {name: outcome.name for name, outcome in outcomes(session).items()}


def show(session: Any) -> None:
    """Print each step's outcome, and the first line of each remote failure."""
    for name, outcome in outcome_names(session).items():
        print(f"  {name:<8} {outcome}")
    for report in session.execution_reports:
        error = report.exc_info[1] if report.exc_info else None
        if isinstance(error, RemoteTaskFailed):
            print(f"\n  {report.task.name}: {str(error).splitlines()[0][:400]}")
            if error.exc_type:
                print(f"  on its node: {error.exc_type}: {error.exc_message}")
    print(f"  exit code: {session.exit_code.name}")


def ran_where(registry: ArtifactRegistry, names: list[str]) -> dict[str, str]:
    """Return the host and pid that each named product's last row recorded."""
    found = {}
    for name in names:
        path = registry.path_for(f"{name}.parquet")
        if path.exists():
            last = pd.read_parquet(path).iloc[-1]
            found[name] = f"{last['host']} pid {last['pid']}"
        else:
            found[name] = "no product"
    return found


def _cluster_options(cluster: str) -> dict[str, Any]:
    """Return the bridge options for ``cluster``."""
    if cluster == "slurm":
        return {"jobmon_cluster": "slurm", "jobmon_resources": SLURM_RESOURCES}
    return {"jobmon_cluster": cluster}


def main(argv: list[str]) -> None:
    """Run ``rebuild <base>`` or ``controller <base> <cluster> <pause>`` from a shell."""
    command, base = argv[0], Path(argv[1])
    print(f"host {socket.gethostname()}, pid {os.getpid()}", flush=True)
    if command == "rebuild":
        tasks, registry = chain(base)
        session = build(tasks, base / "state", jobmon=True, **_cluster_options("slurm"))
        state = session.config.get(RUN_STATE_KEY)
        print(json.dumps({"outcomes": outcome_names(session), "bound": state is not None}))
    elif command == "controller":
        cluster, pause = argv[2], float(argv[3])
        tasks, registry = slow_chain(base, pause)
        session = build(tasks, base / "state", jobmon=True, **_cluster_options(cluster))
        print(json.dumps({"outcomes": outcome_names(session), "time": time.time()}))
    else:
        raise SystemExit(f"unknown command {command!r}")
