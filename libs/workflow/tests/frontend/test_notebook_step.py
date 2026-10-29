"""Acceptance tests for the notebook_step frontend (``frontend.notebook_step``)."""

import json
import sys
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TypeVar, cast

import numpy as np
import pandas as pd
import pytest
from pytask import PathNode, PNode, TaskOutcome

import vivarium.workflow
from vivarium.workflow.backend.build import build, outcomes, run_command
from vivarium.workflow.backend.preview import Reason, preview
from vivarium.workflow.backend.task import Task
from vivarium.workflow.backend.wiring import ArtifactRegistry, WiringError
from vivarium.workflow.frontend.notebook_step import (
    KERNEL_NAME,
    NotebookParameter,
    notebook_step,
)
from vivarium.workflow.frontend.python_step import python_step
from vivarium.workflow.harness import Perturbed, stable_code_id

_V = TypeVar("_V")

_ENV = "notebook-env"
"""The env of steps that are compiled but never built; an env is resolved only at build."""

_EXECUTED_KEY = "executed/report.ipynb"

_NESTED_EXECUTED_KEY = "executed/nested/report.ipynb"

_SOURCE_KEY = "notebooks/source.ipynb"

_NO_SUCH_KERNEL = {
    "name": "no-such-kernel",
    "display_name": "No such kernel",
    "language": "python",
}
"""Notebook kernelspec metadata naming a kernel that is installed nowhere."""

_MISSING_KERNEL_METADATA: dict[str, Any] = {
    "kernelspec": _NO_SUCH_KERNEL,
    "language_info": {"name": "python"},
}
"""Notebook metadata naming a kernel that is installed nowhere, and the python language."""

_DECOY_PYTHON = "/nonexistent/decoy/python"

_DECOY_SPEC: dict[str, Any] = {
    "argv": [_DECOY_PYTHON, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
    "display_name": "Decoy",
    "language": "python",
}
"""A user kernelspec that starts a missing python."""

_DECOY_NAMES = ("python3", KERNEL_NAME)

_JUPYTER_DIR_VARIABLES = ("IPYTHONDIR", "JUPYTER_CONFIG_DIR", "JUPYTER_DATA_DIR")
"""The variables that move IPython's and Jupyter's own directories out of HOME."""

_MARKER = "vivarium-workflow notebook ran"

_FAILURE = "deliberate notebook failure"

_NOTEBOOK_STEP = "report"

_DOWNSTREAM_STEP = "summarize"

_RECORD_KEY = "nested/deeper/record.json"

_TABLE_KEY = "tables/values.csv"

_FINAL_KEY = "final/summary.csv"

_PATH_PARAMETERS = ("raw_path", "record_path", "table_path")

_STR_PARAMETERS: dict[str, NotebookParameter] = {
    "digits": "123",
    "true_word": "True",
    "nan_word": "nan",
    "empty": "",
    "quoted": "two words, 'single' and \"double\" quotes",
    "non_ascii": "café 東京 Ω",
    "dash_led": "--not-a-flag",
}

_TYPED_PARAMETERS: dict[str, NotebookParameter] = {
    "negative": -5,
    "half": 0.5,
    "tiny": 1e-07,
    "flag_on": True,
    "flag_off": False,
    "nothing": None,
}

_YAML_WORD_PARAMETERS: dict[str, NotebookParameter] = {"on": True, "null": None}
"""Parameters named with words that YAML 1.1 reads as a bool and None when unquoted."""

# np.float64 subclasses float at run time, which older numpy stubs do not declare.
_NUMPY_FLOAT = cast(float, np.float64(1 / 3))

# np.int64 is not an int, so NotebookParameter does not admit it, though notebook_step
# accepts any real number.
_NUMPY_INT = cast(int, np.int64(179))

_PARAMETERS: dict[str, NotebookParameter] = {
    **_STR_PARAMETERS,
    **_TYPED_PARAMETERS,
    **_YAML_WORD_PARAMETERS,
    "numpy_float": _NUMPY_FLOAT,
    "numpy_int": _NUMPY_INT,
}

# The shared notebook records the type and repr of every parameter, its working directory
# and its sys.prefix, then writes the raw table doubled into its table product.
_RECORDING_BODY = """
import json
import os
import sys
from pathlib import Path

_values = dict(__VALUES__)
_record = {
    "values": {k: {"type": type(v).__name__, "repr": repr(v)} for k, v in _values.items()},
    "cwd": os.getcwd(),
    "prefix": sys.prefix,
}
Path(record_path).write_text(json.dumps(_record))
_rows = Path(raw_path).read_text().split()
Path(table_path).write_text("n\\n" + "".join(str(int(v) * 2) + "\\n" for v in _rows[1:]))
print("__MARKER__")
"""

_BARE_STEP = "bare_report"

_BARE_EXECUTED_KEY = "executed/{location}/bare.ipynb"
"""An executed key holding '{location}', which names one of the bare step's parameters."""

_BARE_RECORD_KEY = "bare/received.json"

_BARE_PARAMETERS: dict[str, NotebookParameter] = {"location": "179", "draws": 3}

_BARE_DEFAULTS = """
location = 'unset'
draws = 0
received_path = 'unset'
"""

# The notebook without kernel or language metadata records the type and repr of each
# parameter it received.
_BARE_BODY = """
import json
from pathlib import Path

_values = {"location": location, "draws": draws}
_entries = {k: {"type": type(v).__name__, "repr": repr(v)} for k, v in _values.items()}
Path(received_path).write_text(json.dumps({"values": _entries}))
"""

_STALE_STEP = "stale_report"

_STALE_DEFAULTS = """
location = 'unset'
draws = 0
table_path = 'unset'
"""

_STALE_BODY = """
from pathlib import Path

Path(table_path).write_text(f"{location},{draws}")
"""

_INVALID_NAMES = [
    "draw-count",
    "class",
    "__notebook__",
    "__executed__",
    "__code_id__",
    "__command__",
]

_DECLARED_VALUES: dict[str, Any] = {
    "inputs": "raw",
    "produces": "tables/declared.csv",
    "parameters": 1,
}
"""A value to declare a name with in each of notebook_step's three name mappings."""

_SOURCE_ALIAS_KEY = "notebooks/./source.ipynb"
"""A key spelled unlike _SOURCE_KEY that path_for resolves to the same path."""


@dataclass
class _Collision:
    """A produces mapping that gives two of the notebook, executed and products one path."""

    produces: dict[str, str]
    names: tuple[tuple[str, ...], tuple[str, ...]]
    """For each of the two colliding items, the strings any one of which names it."""


# A key spelled with '.' is not a substring of the path it resolves to, so a message that
# names only that path does not count as naming the key.
_COLLISIONS: dict[str, _Collision] = {
    "product_is_executed": _Collision(
        produces={"table_path": "tables/out.csv", "report_path": _EXECUTED_KEY},
        names=(("report_path", _EXECUTED_KEY), (_EXECUTED_KEY,)),
    ),
    "products_share_a_path": _Collision(
        produces={
            "table_path": "tables/out.csv",
            "first_path": "./tables/a.csv",
            "second_path": "tables/./a.csv",
        },
        names=(("first_path", "./tables/a.csv"), ("second_path", "tables/./a.csv")),
    ),
    # The source is written at path_for(_SOURCE_KEY), so its path contains _SOURCE_KEY.
    "product_is_source": _Collision(
        produces={"table_path": "tables/out.csv", "notebook_path": _SOURCE_ALIAS_KEY},
        names=(("notebook_path", _SOURCE_ALIAS_KEY), (_SOURCE_KEY, _SOURCE_ALIAS_KEY)),
    ),
}
"""Products that collide when the source is at path_for(_SOURCE_KEY), keyed by case."""

_REJECTED: dict[str, dict[str, Any]] = {
    "empty_name": {"name": ""},
    "empty_env": {"env": ""},
    "unsupported_value": {"parameters": {"lookup_table": ["a.csv"]}},
    "invalid_name": {"parameters": {"draw-count": 1}},
    "reserved_name": {"parameters": {"__command__": 1}},
    "name_in_two_places": {"parameters": {"table_path": 1}},
    "notebook_not_ipynb": {"notebook": Path(__file__)},
    "missing_notebook": {"notebook": Path("/nonexistent/vivarium-workflow/missing.ipynb")},
    "executed_not_ipynb": {"executed": "executed/report.html"},
    "executed_is_source": {"executed": _SOURCE_KEY},
    **{case: {"produces": collision.produces} for case, collision in _COLLISIONS.items()},
}
"""Overrides of a valid notebook_step call that each make it raise ValueError."""

_ROOT_TABLE_KEY = "supplied/root_table.csv"

_ROOT_NOTEBOOK_KEY = "supplied/root_report.ipynb"


def _offset_table(table: Path) -> pd.DataFrame:
    """Read a notebook's table and add ten to its column."""
    frame = pd.read_csv(table)
    return frame.assign(n=frame["n"] + 10)


def _path_of(node: PNode) -> Path:
    """Return the path of a file node."""
    assert isinstance(node, PathNode)
    assert isinstance(node.path, Path)
    return node.path


def _reversed(mapping: Mapping[str, _V]) -> dict[str, _V]:
    """Return ``mapping``'s entries in reverse insertion order."""
    return dict(reversed(list(mapping.items())))


def _code_cell(cell_id: str, source: str, tags: list[str]) -> dict[str, Any]:
    """Return an nbformat 4.5 code cell that has not been run."""
    return {
        "cell_type": "code",
        "execution_count": None,
        "id": cell_id,
        "metadata": {"tags": tags} if tags else {},
        "outputs": [],
        "source": source,
    }


def _write_notebook(
    path: Path,
    parameters: str = "",
    body: str = "pass",
    metadata: Mapping[str, Any] = _MISSING_KERNEL_METADATA,
) -> Path:
    """Write a parameters cell and a body cell as a notebook with ``metadata``."""
    notebook = {
        "cells": [
            _code_cell("parameters", parameters, ["parameters"]),
            _code_cell("body", body, []),
        ],
        "metadata": dict(metadata),
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(notebook, indent=1), encoding="utf-8")
    return path


def _outputs(notebook: Path) -> list[dict[str, Any]]:
    """Return every cell output saved in the notebook file at ``notebook``."""
    content = json.loads(notebook.read_text(encoding="utf-8"))
    return [output for cell in content["cells"] for output in cell.get("outputs", [])]


def _stream_text(notebook: Path) -> str:
    """Return the text of every stream output saved in the notebook at ``notebook``."""
    texts = [
        output.get("text", "")
        for output in _outputs(notebook)
        if output.get("output_type") == "stream"
    ]
    return "".join(text if isinstance(text, str) else "".join(text) for text in texts)


def _errors(notebook: Path) -> list[tuple[str, str]]:
    """Return the name and value of each error output in the notebook at ``notebook``."""
    return [
        (str(output.get("ename")), str(output.get("evalue")))
        for output in _outputs(notebook)
        if output.get("output_type") == "error"
    ]


def _registry(tmp_path: Path) -> ArtifactRegistry:
    """Return a registry under ``tmp_path`` with the root inputs ``raw`` and ``lookup``."""
    roots: dict[str, Path] = {}
    for key in ("raw", "lookup"):
        roots[key] = tmp_path / f"{key}.csv"
        roots[key].write_text("n\n1\n")
    return ArtifactRegistry(tmp_path / "out", "run1", root_inputs=roots)


def _root_input_registry(tmp_path: Path) -> ArtifactRegistry:
    """Return a registry with a table and a notebook as root inputs, under ``tmp_path``."""
    # Neither key is part of its file's path, so a message naming only the file does not
    # count as naming the key.
    notebook = _write_notebook(tmp_path / "handed_over" / "report.ipynb")
    table = tmp_path / "handed_over" / "table.csv"
    table.write_text("n\n1\n")
    return ArtifactRegistry(
        tmp_path / "out",
        "run1",
        root_inputs={_ROOT_TABLE_KEY: table, _ROOT_NOTEBOOK_KEY: notebook},
    )


def _source(tmp_path: Path) -> Path:
    """Write a minimal source notebook under ``tmp_path`` and return its path."""
    return _write_notebook(tmp_path / "notebooks" / "report.ipynb", "location = 'unset'")


def _command(tmp_path: Path, parameters: Mapping[str, NotebookParameter]) -> str:
    """Return the run command of a notebook step compiled with ``parameters``."""
    task = notebook_step(
        _source(tmp_path),
        executed=_EXECUTED_KEY,
        registry=_registry(tmp_path),
        env=_ENV,
        parameters=parameters,
    )
    assert isinstance(task.run, str)
    return task.run


def _declaring(name: str, where: str) -> dict[str, Any]:
    """Return notebook_step keyword arguments that declare ``name`` in ``where``."""
    return {where: {name: _DECLARED_VALUES[where]}}


def _decoy_kernelspecs(data_dir: Path) -> Path:
    """Write user kernelspecs named 'python3' and KERNEL_NAME that start a missing python."""
    for name in _DECOY_NAMES:
        spec_dir = data_dir / "kernels" / name
        spec_dir.mkdir(parents=True)
        (spec_dir / "kernel.json").write_text(json.dumps(_DECOY_SPEC))
    return data_dir


def _recording_env(prefix: Path, log: Path) -> Path:
    """Create an env at ``prefix`` whose bin/python appends its arguments to ``log``."""
    python = prefix / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{log}'\n")
    python.chmod(0o755)
    log.touch()
    return prefix


def _recording_step(tmp_path: Path, registry: ArtifactRegistry, log: Path) -> Task:
    """Compile a step with nested output keys under an env that logs to ``log``."""
    return notebook_step(
        _source(tmp_path),
        executed=_NESTED_EXECUTED_KEY,
        registry=registry,
        env=str(_recording_env(tmp_path / "stub_env", log)),
        produces={"record_path": _RECORD_KEY, "table_path": _TABLE_KEY},
    )


@pytest.fixture(scope="module", autouse=True)
def isolated_jupyter_dirs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point IPython and Jupyter at empty directories, so no kernel reads or writes HOME."""
    base = tmp_path_factory.mktemp("jupyter_dirs")
    with pytest.MonkeyPatch.context() as patch:
        for variable in _JUPYTER_DIR_VARIABLES:
            directory = base / variable.lower()
            directory.mkdir()
            patch.setenv(variable, str(directory))
        yield


@dataclass
class _SharedRun:
    """The one real build that every papermill-run test reads, and what it observed."""

    outcomes: dict[str, TaskOutcome]
    order: list[str]
    """Task names in the order pytask reported them executed."""
    notebook: Task
    downstream: Task
    registry: ArtifactRegistry
    source: Path
    source_bytes: bytes
    """The source notebook's bytes just before the build."""
    raw: Path
    bare_source: Path
    """The bare step's source notebook, in a directory named with '{location}'."""
    record_dir_existed: bool
    """Whether the record product's parent directory existed just before the build."""
    tmpdir: Path
    """The directory TMPDIR named during the build."""
    decoy: Path
    """The directory JUPYTER_DATA_DIR named during the build, which holds the decoys."""


@pytest.fixture(scope="module")
def shared_run(tmp_path_factory: pytest.TempPathFactory) -> _SharedRun:
    """Build two notebook steps and a python_step reading a product once, against decoys."""
    base = tmp_path_factory.mktemp("notebook_run")
    # Cache this process's temp dir now, so TMPDIR below reaches only the build's
    # subprocesses.
    tempfile.gettempdir()
    raw = base / "input files" / "raw.csv"
    raw.parent.mkdir()
    raw.write_text("n\n1\n2\n3\n")
    names = [*_PATH_PARAMETERS, *_PARAMETERS]
    defaults = "\n".join(f"{name} = 'unset'" for name in names)
    values = ", ".join(f"{name}={name}" for name in names)
    body = _RECORDING_BODY.replace("__VALUES__", values).replace("__MARKER__", _MARKER)
    source = _write_notebook(base / "notebooks" / "report.ipynb", defaults, body)
    registry = ArtifactRegistry(base / "out root", "run1", root_inputs={"raw": raw})
    notebook = notebook_step(
        source,
        executed=_EXECUTED_KEY,
        registry=registry,
        env=sys.prefix,
        inputs={"raw_path": "raw"},
        produces={"record_path": _RECORD_KEY, "table_path": _TABLE_KEY},
        parameters=_PARAMETERS,
        name=_NOTEBOOK_STEP,
    )
    downstream = python_step(
        _offset_table,
        inputs={"table": _TABLE_KEY},
        produces=_FINAL_KEY,
        registry=registry,
        name=_DOWNSTREAM_STEP,
    )
    bare_source = _write_notebook(
        base / "notebooks {location}" / "bare.ipynb", _BARE_DEFAULTS, _BARE_BODY, metadata={}
    )
    bare = notebook_step(
        bare_source,
        executed=_BARE_EXECUTED_KEY,
        registry=registry,
        env=sys.prefix,
        produces={"received_path": _BARE_RECORD_KEY},
        parameters=_BARE_PARAMETERS,
        name=_BARE_STEP,
    )
    decoy = _decoy_kernelspecs(base / "decoy")
    tmpdir = base / "tmpdir"
    tmpdir.mkdir()
    record_dir_existed = registry.path_for(_RECORD_KEY).parent.exists()
    source_bytes = source.read_bytes()

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("JUPYTER_DATA_DIR", str(decoy))
        patch.setenv("TMPDIR", str(tmpdir))
        session = build([notebook, downstream, bare], base / "state")

    return _SharedRun(
        outcomes=outcomes(session),
        order=[report.task.name for report in session.execution_reports],
        notebook=notebook,
        downstream=downstream,
        registry=registry,
        source=source,
        source_bytes=source_bytes,
        raw=raw,
        bare_source=bare_source,
        record_dir_existed=record_dir_existed,
        tmpdir=tmpdir,
        decoy=decoy,
    )


def _recorded(
    run: _SharedRun, step: str = _NOTEBOOK_STEP, key: str = _RECORD_KEY
) -> dict[str, Any]:
    """Return what shared ``step`` recorded at ``key``, once the step has succeeded."""
    assert run.outcomes.get(step) == TaskOutcome.SUCCESS, run.outcomes
    record: dict[str, Any] = json.loads(run.registry.path_for(key).read_text())
    return record


def _seen(record: dict[str, Any], name: str) -> tuple[str, str]:
    """Return the type name and repr the notebook recorded for parameter ``name``."""
    entry = record["values"][name]
    return str(entry["type"]), str(entry["repr"])


def _expected(value: object) -> tuple[str, str]:
    """Return the type name and repr of a value that arrives unchanged."""
    return type(value).__name__, repr(value)


@dataclass
class _Converged:
    """A one-step notebook pipeline built once, with a stable code identity."""

    source: Path
    output_root: Path
    state: Path
    first: dict[str, TaskOutcome] = field(default_factory=dict)
    """The outcomes of the first build."""

    def tasks(self, location: str = "179") -> list[Task]:
        """Compile the step afresh with ``location`` and a stable code identity."""
        task = notebook_step(
            self.source,
            executed="executed/stale.ipynb",
            registry=ArtifactRegistry(self.output_root, "run1"),
            env=sys.prefix,
            produces={"table_path": "tables/stale.csv"},
            parameters={"location": location, "draws": 3},
            name=_STALE_STEP,
        )
        return [replace(task, code_id=stable_code_id(f"{task.name}::code"))]


@pytest.fixture(scope="module")
def converged(tmp_path_factory: pytest.TempPathFactory) -> _Converged:
    """Build the one-step notebook pipeline once, with a stable code identity."""
    base = tmp_path_factory.mktemp("notebook_staleness")
    source = _write_notebook(
        base / "notebooks" / "stale.ipynb",
        _STALE_DEFAULTS,
        _STALE_BODY,
    )
    built = _Converged(source=source, output_root=base / "out", state=base / "state")
    built.first = outcomes(build(built.tasks(), built.state))
    return built


# Compilation: the task notebook_step returns


def test_notebook_step_returns_one_command_task_under_the_declared_env(
    tmp_path: Path,
) -> None:
    """notebook_step returns one Task whose run is a str, whose env is the declared env, and whose resources are carried (empty when omitted)."""
    registry, source = _registry(tmp_path), _source(tmp_path)
    resources: dict[str, Any] = {"cores": 2, "memory": "4G", "runtime": "1h"}

    task = notebook_step(
        source,
        executed=_EXECUTED_KEY,
        registry=registry,
        env=_ENV,
        resources=resources,
    )
    bare = notebook_step(source, executed="executed/bare.ipynb", registry=registry, env=_ENV)

    assert isinstance(task, Task)
    assert isinstance(task.run, str)
    assert task.env == _ENV
    assert task.resources == resources
    assert bare.resources == {}


def test_name_defaults_to_the_executed_key_and_can_be_overridden(tmp_path: Path) -> None:
    """The task name is the executed key by default and the given name when name= is passed."""
    registry, source = _registry(tmp_path), _source(tmp_path)

    unnamed = notebook_step(source, executed="executed/a.ipynb", registry=registry, env=_ENV)
    named = notebook_step(
        source,
        executed="executed/b.ipynb",
        registry=registry,
        env=_ENV,
        name="validate",
    )

    assert unnamed.name == "executed/a.ipynb"
    assert named.name == "validate"


def test_inputs_are_the_source_notebook_and_the_resolved_input_nodes(tmp_path: Path) -> None:
    """Task.inputs holds the source notebook's file node under '__notebook__' and, under each parameter name, the producing step's own node for a produced key or a node at the file's path for a root input."""
    registry, source = _registry(tmp_path), _source(tmp_path)
    registry.register_output("upstream/table.csv")

    task = notebook_step(
        source,
        executed=_EXECUTED_KEY,
        registry=registry,
        env=_ENV,
        inputs={"raw_path": "raw", "table_path": "upstream/table.csv"},
    )

    assert set(task.inputs) == {"__notebook__", "raw_path", "table_path"}
    assert _path_of(task.inputs["__notebook__"]) == source.resolve()
    assert task.inputs["table_path"] is registry.resolve_input("upstream/table.csv")
    # A root input resolves to a new node on each call, so only its path can match.
    assert _path_of(task.inputs["raw_path"]) == _path_of(registry.resolve_input("raw"))


def test_outputs_are_the_executed_notebook_and_the_product_nodes(tmp_path: Path) -> None:
    """Task.outputs holds the registry's node for the executed key under '__executed__' and the registry's node for each produces key under its parameter name."""
    registry, source = _registry(tmp_path), _source(tmp_path)
    produces = {"table_path": "tables/values.csv", "figure_path": "figures/plot.png"}

    task = notebook_step(
        source,
        executed=_EXECUTED_KEY,
        registry=registry,
        env=_ENV,
        produces=produces,
    )

    assert set(task.outputs) == {"__executed__", *produces}
    executed = task.outputs["__executed__"]
    assert executed is registry.resolve_input(_EXECUTED_KEY)
    assert _path_of(executed) == registry.path_for(_EXECUTED_KEY)
    for name, key in produces.items():
        assert task.outputs[name] is registry.resolve_input(key)


def test_inputs_produces_and_parameters_are_optional(tmp_path: Path) -> None:
    """A notebook step with no inputs, products or parameters compiles, with only the source notebook as input and the executed notebook as output."""
    registry, source = _registry(tmp_path), _source(tmp_path)

    task = notebook_step(source, executed=_EXECUTED_KEY, registry=registry, env=_ENV)

    assert set(task.inputs) == {"__notebook__"}
    assert _path_of(task.inputs["__notebook__"]) == source.resolve()
    assert set(task.outputs) == {"__executed__"}
    assert task.outputs["__executed__"] is registry.resolve_input(_EXECUTED_KEY)


def test_command_is_identical_for_equal_arguments_in_any_order(tmp_path: Path) -> None:
    """Two calls whose inputs, produces and parameters hold the same entries in different insertion orders give identical run commands."""
    source = _source(tmp_path)
    inputs = {"raw_path": "raw", "lookup_path": "lookup"}
    produces = {"table_path": "tables/values.csv", "figure_path": "figures/plot.png"}
    parameters: dict[str, NotebookParameter] = {
        "location": "179",
        "draws": 10,
        "scale": 0.5,
        "verbose": True,
        "seed": None,
    }

    forward = notebook_step(
        source,
        executed=_EXECUTED_KEY,
        registry=_registry(tmp_path),
        env=_ENV,
        inputs=inputs,
        produces=produces,
        parameters=parameters,
    )
    backward = notebook_step(
        source,
        executed=_EXECUTED_KEY,
        registry=_registry(tmp_path),
        env=_ENV,
        inputs=_reversed(inputs),
        produces=_reversed(produces),
        parameters=_reversed(parameters),
    )

    assert isinstance(forward.run, str)
    assert forward.run == backward.run


def test_command_changes_when_a_parameter_value_changes(tmp_path: Path) -> None:
    """Changing one parameter's value, including only its type (the str '1' versus the int 1), gives a different run command."""
    baseline = _command(tmp_path, {"location": "1", "draws": 10})

    # A control: equal arguments compile to an equal command, so a difference is real.
    assert _command(tmp_path, {"location": "1", "draws": 10}) == baseline
    assert _command(tmp_path, {"location": "2", "draws": 10}) != baseline
    assert _command(tmp_path, {"location": 1, "draws": 10}) != baseline


def test_command_names_no_installation_path(tmp_path: Path) -> None:
    """The run command contains neither the pipeline interpreter's path, nor the declared env's prefix, nor the vivarium.workflow package directory."""
    task = notebook_step(
        _source(tmp_path),
        executed=_EXECUTED_KEY,
        registry=_registry(tmp_path),
        env=sys.prefix,
        inputs={"raw_path": "raw"},
        produces={"table_path": "tables/values.csv"},
        parameters={"location": "179"},
    )
    package = Path(vivarium.workflow.__file__).parent
    paths = [Path(sys.executable), Path(sys.prefix), package]
    installed = {str(form) for path in paths for form in (path, path.resolve())}

    assert isinstance(task.run, str)
    assert sorted(name for name in installed if name in task.run) == []


def test_command_creates_parent_directories_itself(tmp_path: Path) -> None:
    """Run with run_command under a stub env whose bin/python only records its arguments, outside pytask, the command creates the parent directories of the executed notebook and of every product."""
    registry = _registry(tmp_path)
    task = _recording_step(tmp_path, registry, tmp_path / "python_calls.log")
    keys = [_NESTED_EXECUTED_KEY, _RECORD_KEY, _TABLE_KEY]
    parents = [registry.path_for(key).parent for key in keys]
    assert [parent for parent in parents if parent.exists()] == []

    assert isinstance(task.run, str)
    run_command(task.run, task.env)

    assert [parent for parent in parents if not parent.is_dir()] == []


def test_command_runs_papermill_with_the_declared_env_python(tmp_path: Path) -> None:
    """Run with run_command under a stub env whose bin/python only records its arguments, the command runs 'python -m papermill' with that stub env's python."""
    log = tmp_path / "python_calls.log"
    task = _recording_step(tmp_path, _registry(tmp_path), log)

    assert isinstance(task.run, str)
    run_command(task.run, task.env)

    calls = [line.split() for line in log.read_text().splitlines()]
    assert any(("-m", "papermill") in zip(args, args[1:]) for args in calls), calls


# A real papermill run (the test env is the declared env)


def test_executed_notebook_lands_at_its_registry_path_and_the_source_is_unchanged(
    shared_run: _SharedRun,
) -> None:
    """After a build the executed notebook exists at registry.path_for(executed), holds the run's outputs, and the source notebook's bytes are unchanged."""
    executed = shared_run.registry.path_for(_EXECUTED_KEY)

    assert shared_run.outcomes.get(_NOTEBOOK_STEP) == TaskOutcome.SUCCESS
    assert executed.is_file()
    assert _MARKER in _stream_text(executed)
    assert shared_run.source.read_bytes() == shared_run.source_bytes


def test_product_is_written_into_a_directory_the_step_created(
    shared_run: _SharedRun,
) -> None:
    """A product whose parent directory did not exist before the build is written by the notebook at its registry path."""
    assert not shared_run.record_dir_existed

    record = _recorded(shared_run)

    assert shared_run.registry.path_for(_RECORD_KEY).is_file()
    assert "values" in record  # the file is the notebook's own record


def test_input_and_product_paths_reach_the_notebook_as_str(shared_run: _SharedRun) -> None:
    """Each input and product parameter arrives in the notebook as a str equal to its resolved path, including a path that contains a space."""
    record = _recorded(shared_run)
    expected = {
        "raw_path": str(shared_run.raw.resolve()),
        "record_path": str(shared_run.registry.path_for(_RECORD_KEY)),
        "table_path": str(shared_run.registry.path_for(_TABLE_KEY)),
    }

    assert all(" " in path for path in expected.values())
    seen = {name: _seen(record, name) for name in expected}
    assert seen == {name: _expected(path) for name, path in expected.items()}


def test_str_parameters_arrive_unchanged(shared_run: _SharedRun) -> None:
    """The str values '123', 'True', 'nan', '', a value with spaces and both quote characters, a non-ASCII value, and a value starting with '-' arrive as the identical str."""
    record = _recorded(shared_run)

    seen = {name: _seen(record, name) for name in _STR_PARAMETERS}

    assert seen == {name: _expected(value) for name, value in _STR_PARAMETERS.items()}


def test_number_bool_and_none_parameters_keep_their_types(shared_run: _SharedRun) -> None:
    """-5 arrives as int, 0.5 and 1e-07 as float, True and False as bool, and None as None, each equal to the value passed."""
    record = _recorded(shared_run)

    seen = {name: _seen(record, name) for name in _TYPED_PARAMETERS}

    assert seen == {name: _expected(value) for name, value in _TYPED_PARAMETERS.items()}


def test_numpy_float_parameter_arrives_as_a_float(shared_run: _SharedRun) -> None:
    """A numpy float64 parameter arrives as a float equal to its value."""
    record = _recorded(shared_run)

    assert _seen(record, "numpy_float") == ("float", repr(float(_NUMPY_FLOAT)))


def test_notebook_runs_in_the_source_notebook_directory(shared_run: _SharedRun) -> None:
    """The notebook's working directory during the run is the source notebook's directory."""
    record = _recorded(shared_run)

    assert Path(record["cwd"]).resolve() == shared_run.source.parent.resolve()


def test_notebook_runs_on_the_env_python_despite_other_kernelspecs(
    shared_run: _SharedRun,
) -> None:
    """With decoy user kernelspecs named 'python3' and KERNEL_NAME that point at a missing interpreter, and a notebook whose metadata names a kernel that does not exist, the notebook runs and its sys.prefix is the declared env's prefix."""
    record = _recorded(shared_run)

    assert Path(record["prefix"]).resolve() == Path(sys.prefix).resolve()


def test_run_leaves_no_temporary_kernelspec_behind(shared_run: _SharedRun) -> None:
    """After the run, the temporary directory (TMPDIR) the command used holds nothing it created, the decoy user kernelspecs are unchanged, and the env has no kernelspec named KERNEL_NAME."""
    kernels = shared_run.decoy / "kernels"
    env_kernelspec = Path(sys.prefix, "share", "jupyter", "kernels", KERNEL_NAME)

    assert shared_run.outcomes.get(_NOTEBOOK_STEP) == TaskOutcome.SUCCESS
    assert sorted(shared_run.tmpdir.iterdir()) == []
    assert sorted(spec.name for spec in kernels.iterdir()) == sorted(_DECOY_NAMES)
    for name in _DECOY_NAMES:
        assert json.loads((kernels / name / "kernel.json").read_text()) == _DECOY_SPEC
    assert not env_kernelspec.exists(), env_kernelspec


def test_downstream_python_step_reads_a_notebook_product(shared_run: _SharedRun) -> None:
    """A python_step that names a notebook product key gets the edge, runs after the notebook step, and reads the data the notebook wrote."""
    notebook, downstream = shared_run.notebook, shared_run.downstream
    order = shared_run.order

    assert downstream.inputs["table"] is notebook.outputs["table_path"]
    assert shared_run.outcomes.get(_NOTEBOOK_STEP) == TaskOutcome.SUCCESS
    assert shared_run.outcomes.get(_DOWNSTREAM_STEP) == TaskOutcome.SUCCESS
    assert order.index(_NOTEBOOK_STEP) < order.index(_DOWNSTREAM_STEP)
    final = pd.read_csv(shared_run.registry.path_for(_FINAL_KEY))
    assert final["n"].tolist() == [12, 14, 16]  # raw doubled by the notebook, then plus ten


def test_bool_and_none_parameters_named_like_yaml_words_keep_their_types(
    shared_run: _SharedRun,
) -> None:
    """A bool parameter named 'on' and a None parameter named 'null', names that YAML 1.1 reads as a bool and None when unquoted, arrive under those names as True and None."""
    record = _recorded(shared_run)

    seen = {name: _seen(record, name) for name in _YAML_WORD_PARAMETERS}

    assert seen == {name: _expected(value) for name, value in _YAML_WORD_PARAMETERS.items()}


def test_numpy_integer_parameter_arrives_as_an_int(shared_run: _SharedRun) -> None:
    """A numpy int64 parameter arrives as an int equal to its value."""
    record = _recorded(shared_run)

    assert _seen(record, "numpy_int") == ("int", repr(int(_NUMPY_INT)))


def test_notebook_without_kernel_or_language_metadata_runs(shared_run: _SharedRun) -> None:
    """A notebook whose metadata names no kernel and no language runs and receives its parameters."""
    record = _recorded(shared_run, _BARE_STEP, _BARE_RECORD_KEY)

    seen = {name: _seen(record, name) for name in _BARE_PARAMETERS}

    assert seen == {name: _expected(value) for name, value in _BARE_PARAMETERS.items()}


def test_paths_with_braces_are_used_literally(shared_run: _SharedRun) -> None:
    """A source notebook and an executed key whose paths contain '{location}', in a step with a parameter named location, are read and written at those literal paths."""
    executed = shared_run.registry.path_for(_BARE_EXECUTED_KEY)
    literal = [shared_run.bare_source, executed]
    # The paths papermill would use if it formatted them with the step's parameters.
    formatted = [Path(str(path).format(**_BARE_PARAMETERS)) for path in literal]
    assert all("{location}" in str(path) for path in literal)
    assert all("{location}" not in str(path) for path in formatted)

    assert shared_run.outcomes.get(_BARE_STEP) == TaskOutcome.SUCCESS, shared_run.outcomes
    assert executed.is_file()
    assert [path for path in formatted if path.exists()] == []


# Staleness, with a stable code identity standing in for a code fingerprint


def test_unchanged_notebook_step_skips_on_rebuild(converged: _Converged) -> None:
    """With stable_code_id as the code identity, a second build with the same notebook and parameters reports SKIP_UNCHANGED for the notebook step."""
    assert converged.first == {_STALE_STEP: TaskOutcome.SUCCESS}

    rebuilt = outcomes(build(converged.tasks(), converged.state))

    assert rebuilt == {_STALE_STEP: TaskOutcome.SKIP_UNCHANGED}


def test_changed_parameter_makes_preview_report_the_command_changed(
    converged: _Converged,
) -> None:
    """After a build, a preview with one parameter changed reports the step as would-run with '<step>::__command__' among its reasons."""
    assert converged.first == {_STALE_STEP: TaskOutcome.SUCCESS}

    [step] = preview(converged.tasks(location="180"), converged.state)

    command = f"{_STALE_STEP}::__command__"
    assert step.would_run
    assert Reason(node=command, kind="changed") in step.reasons, step.reasons


def test_edited_source_notebook_makes_preview_report_it_changed(
    converged: _Converged,
) -> None:
    """After a build, a preview with the source notebook edited reports the step as would-run with the notebook's path among its reasons."""
    assert converged.first == {_STALE_STEP: TaskOutcome.SUCCESS}

    with Perturbed(converged.source, b"\n"):
        [step] = preview(converged.tasks(), converged.state)

    notebook_path = converged.source.resolve().as_posix()
    assert step.would_run
    assert Reason(node=notebook_path, kind="changed") in step.reasons, step.reasons


# Failures at run time


def test_failing_notebook_fails_the_step_and_keeps_the_executed_notebook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A notebook that raises makes the step FAIL and its downstream step SKIP_PREVIOUS_FAILED, leaves the executed notebook with the error at its product path, and leaves nothing in TMPDIR."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    source = _write_notebook(
        tmp_path / "notebooks" / "fails.ipynb",
        "table_path = 'unset'",
        f"raise RuntimeError({_FAILURE!r})",
    )
    notebook = notebook_step(
        source,
        executed="executed/fails.ipynb",
        registry=registry,
        env=sys.prefix,
        produces={"table_path": _TABLE_KEY},
        name="fails",
    )
    downstream = python_step(
        _offset_table,
        inputs={"table": _TABLE_KEY},
        produces=_FINAL_KEY,
        registry=registry,
        name=_DOWNSTREAM_STEP,
    )
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir()
    # Cache this process's temp dir first, so TMPDIR reaches only the build's subprocesses.
    tempfile.gettempdir()
    monkeypatch.setenv("TMPDIR", str(tmpdir))

    assert outcomes(build([notebook, downstream], tmp_path / "state")) == {
        "fails": TaskOutcome.FAIL,
        _DOWNSTREAM_STEP: TaskOutcome.SKIP_PREVIOUS_FAILED,
    }
    executed = registry.path_for("executed/fails.ipynb")
    assert executed.is_file()
    assert ("RuntimeError", _FAILURE) in _errors(executed)
    assert sorted(tmpdir.iterdir()) == []


def test_notebook_that_does_not_write_a_declared_product_fails_the_step(
    tmp_path: Path,
) -> None:
    """A notebook that finishes without writing one of its declared products makes the step FAIL."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    source = _write_notebook(
        tmp_path / "notebooks" / "partial.ipynb",
        "written_path = 'unset'\nskipped_path = 'unset'",
        "from pathlib import Path\nPath(written_path).write_text('written')",
    )
    task = notebook_step(
        source,
        executed="executed/partial.ipynb",
        registry=registry,
        env=sys.prefix,
        produces={"written_path": "out/written.txt", "skipped_path": "out/skipped.txt"},
        name="partial",
    )

    assert outcomes(build([task], tmp_path / "state")) == {"partial": TaskOutcome.FAIL}
    assert registry.path_for("out/written.txt").is_file()  # the notebook ran to its end
    assert not registry.path_for("out/skipped.txt").exists()


def test_unresolved_env_is_refused_before_anything_runs(tmp_path: Path) -> None:
    """build raises ValueError naming the step and the env when the declared env does not resolve, and no executed notebook is written."""
    missing_env = str(tmp_path / "no_such_env")
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    task = notebook_step(
        _source(tmp_path),
        executed=_EXECUTED_KEY,
        registry=registry,
        env=missing_env,
        name="unresolved_report",
    )

    with pytest.raises(ValueError) as info:
        build([task], tmp_path / "state")

    message = str(info.value)
    assert "unresolved_report" in message
    assert missing_env in message
    assert not registry.path_for(_EXECUTED_KEY).exists()


# Definition-time errors


def test_empty_name_or_env_is_rejected(tmp_path: Path) -> None:
    """An empty name or an empty env raises ValueError."""
    source = _source(tmp_path)

    with pytest.raises(ValueError):
        notebook_step(
            source,
            executed=_EXECUTED_KEY,
            registry=_registry(tmp_path),
            env=_ENV,
            name="",
        )
    with pytest.raises(ValueError):
        notebook_step(source, executed=_EXECUTED_KEY, registry=_registry(tmp_path), env="")


@pytest.mark.parametrize(
    "value",
    [["a.csv"], {"path": "a.csv"}, ("a.csv", "b.csv"), Path("a.csv")],
    ids=["list", "dict", "tuple", "path"],
)
def test_parameter_values_of_unsupported_types_are_rejected(
    tmp_path: Path,
    value: object,
) -> None:
    """A list, dict, tuple or Path parameter value raises ValueError naming the parameter and saying to pass files through inputs."""
    parameters: dict[str, Any] = {"lookup_table": value}

    with pytest.raises(ValueError) as info:
        notebook_step(
            _source(tmp_path),
            executed=_EXECUTED_KEY,
            registry=_registry(tmp_path),
            env=_ENV,
            parameters=parameters,
        )

    message = str(info.value)
    assert "lookup_table" in message
    assert "inputs" in message


@pytest.mark.parametrize("where", ["inputs", "produces", "parameters"])
@pytest.mark.parametrize("bad_name", _INVALID_NAMES)
def test_invalid_parameter_names_are_rejected(
    tmp_path: Path,
    bad_name: str,
    where: str,
) -> None:
    """A name that is not an identifier, a keyword such as 'class', or a reserved name ('__notebook__', '__executed__', '__code_id__', '__command__') in inputs, produces or parameters raises ValueError naming it."""
    with pytest.raises(ValueError) as info:
        notebook_step(
            _source(tmp_path),
            executed=_EXECUTED_KEY,
            registry=_registry(tmp_path),
            env=_ENV,
            **_declaring(bad_name, where),
        )

    assert bad_name in str(info.value)


@pytest.mark.parametrize(
    "first, second",
    [("inputs", "produces"), ("inputs", "parameters"), ("produces", "parameters")],
)
def test_a_name_used_in_two_of_inputs_produces_and_parameters_is_rejected(
    tmp_path: Path,
    first: str,
    second: str,
) -> None:
    """A parameter name that appears in more than one of inputs, produces and parameters raises ValueError naming it."""
    with pytest.raises(ValueError) as info:
        notebook_step(
            _source(tmp_path),
            executed=_EXECUTED_KEY,
            registry=_registry(tmp_path),
            env=_ENV,
            **_declaring("location", first),
            **_declaring("location", second),
        )

    assert "location" in str(info.value)


def test_source_notebook_must_be_an_existing_ipynb_file(tmp_path: Path) -> None:
    """A source path that does not end in .ipynb, or that does not exist, raises ValueError naming the path."""
    wrong_suffix = tmp_path / "analysis.py"
    wrong_suffix.write_text("location = 'unset'\n")
    sources: list[Path | str] = [wrong_suffix, str(tmp_path / "missing.ipynb")]

    for source in sources:
        with pytest.raises(ValueError) as info:
            notebook_step(
                source,
                executed=_EXECUTED_KEY,
                registry=_registry(tmp_path),
                env=_ENV,
            )
        assert str(source) in str(info.value)


def test_executed_key_must_end_in_ipynb(tmp_path: Path) -> None:
    """An executed key that does not end in .ipynb raises ValueError naming the key."""
    with pytest.raises(ValueError) as info:
        notebook_step(
            _source(tmp_path),
            executed="executed/report.html",
            registry=_registry(tmp_path),
            env=_ENV,
        )

    assert "executed/report.html" in str(info.value)


def test_executed_key_that_resolves_to_the_source_path_is_rejected(tmp_path: Path) -> None:
    """An executed key whose registry path is the source notebook's path raises ValueError."""
    registry = _registry(tmp_path)
    source = _write_notebook(registry.path_for(_SOURCE_KEY))

    with pytest.raises(ValueError):
        notebook_step(source, executed=_SOURCE_KEY, registry=registry, env=_ENV)


def test_unknown_input_key_raises_wiring_error(tmp_path: Path) -> None:
    """An input key with no producer and no root input raises WiringError with a did-you-mean suggestion."""
    registry = _registry(tmp_path)
    registry.register_output("tables/summary.csv")

    with pytest.raises(WiringError, match=r"Did you mean 'tables/summary\.csv'"):
        notebook_step(
            _source(tmp_path),
            executed=_EXECUTED_KEY,
            registry=registry,
            env=_ENV,
            inputs={"summary_path": "tables/sumary.csv"},
        )


@pytest.mark.parametrize("override", list(_REJECTED.values()), ids=list(_REJECTED))
def test_definition_errors_register_nothing(
    tmp_path: Path,
    override: dict[str, Any],
) -> None:
    """After a ValueError from notebook_step, resolving the executed key or a produces key as an input raises WiringError, because nothing was registered."""
    registry = _registry(tmp_path)
    call: dict[str, Any] = {
        "notebook": _write_notebook(registry.path_for(_SOURCE_KEY)),
        "executed": _EXECUTED_KEY,
        "registry": registry,
        "env": _ENV,
        "inputs": {"raw_path": "raw"},
        "produces": {"table_path": "tables/out.csv"},
        "parameters": {"location": "179"},
        **override,
    }

    executed: str = call["executed"]

    with pytest.raises(ValueError):
        notebook_step(**call)

    for key in (executed, "tables/out.csv"):
        with pytest.raises(WiringError):
            registry.resolve_input(key)


def test_numpy_bool_parameter_is_rejected_with_a_conversion_hint(tmp_path: Path) -> None:
    """A numpy bool_ parameter value raises ValueError naming the parameter and saying to convert it with bool()."""
    parameters: dict[str, Any] = {"use_cache": np.bool_(True)}

    with pytest.raises(ValueError) as info:
        notebook_step(
            _source(tmp_path),
            executed=_EXECUTED_KEY,
            registry=_registry(tmp_path),
            env=_ENV,
            parameters=parameters,
        )

    message = str(info.value)
    assert "use_cache" in message
    assert "bool()" in message
    assert "type numpy.bool" in message


@pytest.mark.parametrize("collision", list(_COLLISIONS.values()), ids=list(_COLLISIONS))
def test_colliding_output_paths_are_rejected(tmp_path: Path, collision: _Collision) -> None:
    """A produces key equal to the executed key, two produces keys that resolve to one path, or a produces key that resolves to the source notebook raises ValueError naming both colliding items."""
    registry = _registry(tmp_path)
    source = _write_notebook(registry.path_for(_SOURCE_KEY))

    with pytest.raises(ValueError) as info:
        notebook_step(
            source,
            executed=_EXECUTED_KEY,
            registry=registry,
            env=_ENV,
            produces=collision.produces,
        )

    message = str(info.value)
    unnamed = [
        names for names in collision.names if not any(name in message for name in names)
    ]
    assert unnamed == [], message


def test_executed_or_product_key_that_is_a_root_input_raises_wiring_error(
    tmp_path: Path,
) -> None:
    """An executed key or a produces key that is a declared root input raises WiringError naming the key."""
    source = _source(tmp_path)

    with pytest.raises(WiringError) as executed_info:
        notebook_step(
            source,
            executed=_ROOT_NOTEBOOK_KEY,
            registry=_root_input_registry(tmp_path),
            env=_ENV,
        )
    with pytest.raises(WiringError) as product_info:
        notebook_step(
            source,
            executed=_EXECUTED_KEY,
            registry=_root_input_registry(tmp_path),
            env=_ENV,
            produces={"table_path": _ROOT_TABLE_KEY},
        )

    assert _ROOT_NOTEBOOK_KEY in str(executed_info.value)
    assert _ROOT_TABLE_KEY in str(product_info.value)
