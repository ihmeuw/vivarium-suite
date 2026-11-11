"""Acceptance tests for the notebook_step frontend (``frontend.notebook_step``)."""

import ast
import json
import subprocess
import sys
import tempfile
import warnings
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

import numpy as np
import pandas as pd
import pytest
from pytask import NodeNotFoundError, PathNode, PNode, TaskOutcome

import vivarium.dagger
from tests.frontend.notebooks import (
    broken_env,
    code_cell,
    error_output,
    isolated_jupyter_dirs,
    recorded_errors,
    text_cell,
    write_notebook,
    write_source_notebook,
)
from vivarium.dagger.backend.build import build, outcomes, run_command
from vivarium.dagger.backend.preview import Reason, preview
from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import ArtifactRegistry, WiringError
from vivarium.dagger.frontend.notebook_step import (
    KERNEL_NAME,
    NotebookParameter,
    describe_notebook_failure,
    notebook_step,
)
from vivarium.dagger.frontend.python_step import python_step
from vivarium.dagger.harness import Perturbed, with_stable_code_id

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

_MARKER = "vivarium-dagger notebook ran"

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

_SOURCE_NAMES = (
    "draws",
    "figure_path",
    "location",
    "lookup_path",
    "raw_path",
    "record_path",
    "scale",
    "seed",
    "summary_path",
    "table_path",
    "verbose",
)
"""The names _source's parameters cell assigns: every name a test passes over it."""

_BASE_PARAMETERS = "raw_path = None\ntable_path = None\nlocation = None"
"""A parameters cell assigning each name the base call of the definition-error tests passes."""


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
    "products_share_a_key": _Collision(
        produces={
            "table_path": "tables/out.csv",
            "first_path": "tables/same.csv",
            "second_path": "tables/same.csv",
        },
        names=(("first_path",), ("second_path",)),
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
    "missing_notebook": {"notebook": Path("/nonexistent/vivarium-dagger/missing.ipynb")},
    "executed_not_ipynb": {"executed": "executed/report.html"},
    "executed_is_source": {"executed": _SOURCE_KEY},
    "unassigned_parameter": {"parameters": {"loaction": "179"}},
    **{case: {"produces": collision.produces} for case, collision in _COLLISIONS.items()},
}
"""Overrides of a valid notebook_step call that each make it raise ValueError."""

_PARAMETERS_CELL_MESSAGES = (
    "does not assign",
    "has no cell tagged",
    "cannot be read as a notebook",
    "is not valid Python",
    "must be a code cell",
)
"""A fragment of each message the parameters-cell check raises."""

_NotebookContent = list[dict[str, Any]] | str | bytes
"""A source notebook file's content: cells for write_notebook, or text or bytes as is."""


def _notebook_text(cells: object, ensure_ascii: bool = True) -> str:
    """Return nbformat 4 JSON text whose top-level 'cells' entry is ``cells``, of any shape."""
    notebook = {"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
    return json.dumps(notebook, indent=1, ensure_ascii=ensure_ascii)


_UNREADABLE_NOTEBOOKS: dict[str, _NotebookContent] = {
    "not_json": "this is not JSON {",
    "not_utf8": _notebook_text(
        [
            code_cell(_BASE_PARAMETERS, tags=["parameters"]),
            code_cell("label = 'café'"),
        ],
        ensure_ascii=False,
    ).encode("latin-1"),
    "top_level_not_an_object": "[]",
    "nbformat_3_worksheets": json.dumps(
        {
            "worksheets": [{"cells": [code_cell(_BASE_PARAMETERS, tags=["parameters"])]}],
            "metadata": {},
            "nbformat": 3,
            "nbformat_minor": 0,
        }
    ),
    "cells_not_a_list": _notebook_text(
        {"0": code_cell(_BASE_PARAMETERS, tags=["parameters"])}
    ),
    "cell_not_an_object": _notebook_text(
        ["not a cell", code_cell(_BASE_PARAMETERS, tags=["parameters"])]
    ),
    "metadata_not_an_object": [
        {**code_cell("x = 1"), "metadata": ["parameters"]},
        code_cell(_BASE_PARAMETERS, tags=["parameters"]),
    ],
    "tags_not_a_list": [{**code_cell(_BASE_PARAMETERS), "metadata": {"tags": "parameters"}}],
    "parameters_source_not_text": [{**code_cell("", tags=["parameters"]), "source": 5}],
}
"""Source notebook files that cannot be read as a notebook, keyed by case.

Each would hold a parameters cell assigning every name of the base call, if it were read
leniently.
"""

_REJECTED_NOTEBOOKS: dict[str, _NotebookContent] = {
    "no_parameters_cell": [code_cell(_BASE_PARAMETERS)],
    "parameters_cell_not_python": [
        code_cell(f"%matplotlib inline\n{_BASE_PARAMETERS}", tags=["parameters"])
    ],
    "parameters_cell_is_markdown": [
        text_cell("markdown", _BASE_PARAMETERS, tags=["parameters"]),
        code_cell(_BASE_PARAMETERS, tags=["parameters"]),
    ],
    "parameters_cell_is_raw": [
        text_cell("raw", _BASE_PARAMETERS, tags=["parameters"]),
        code_cell(_BASE_PARAMETERS, tags=["parameters"]),
    ],
    **_UNREADABLE_NOTEBOOKS,
}
"""Source notebook files that make the base call raise ValueError, keyed by case."""

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


def _write_notebook(
    path: Path,
    parameters: str = "",
    body: str = "pass",
    metadata: Mapping[str, Any] = _MISSING_KERNEL_METADATA,
) -> Path:
    """Write a parameters cell and a body cell as a notebook with ``metadata``."""
    cells = [code_cell(parameters, tags=["parameters"]), code_cell(body)]
    return write_notebook(path, cells, metadata)


def _assigning(names: Iterable[str]) -> str:
    """Return parameters cell source that assigns None to each of ``names``."""
    return "".join(f"{name} = None\n" for name in names)


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
    """Write a source notebook whose parameters cell assigns every name in _SOURCE_NAMES."""
    defaults = "\n".join(f"{name} = 'unset'" for name in _SOURCE_NAMES)
    return _write_notebook(tmp_path / "notebooks" / "report.ipynb", defaults)


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
def module_jupyter_dirs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point IPython and Jupyter at empty directories, so no kernel reads or writes HOME."""
    with isolated_jupyter_dirs(tmp_path_factory.mktemp("jupyter_dirs")):
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
        return [with_stable_code_id(task)]


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
    """The run command contains neither the pipeline interpreter's path, nor the declared env's prefix, nor the vivarium.dagger package directory."""
    task = notebook_step(
        _source(tmp_path),
        executed=_EXECUTED_KEY,
        registry=_registry(tmp_path),
        env=sys.prefix,
        inputs={"raw_path": "raw"},
        produces={"table_path": "tables/values.csv"},
        parameters={"location": "179"},
    )
    package = Path(vivarium.dagger.__file__).parent
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
    assert ("RuntimeError", _FAILURE) in recorded_errors(executed)
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

    message = str(info.value)
    assert f"parameter name {bad_name!r} in {where}" in message, message


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


def _base_call(registry: ArtifactRegistry, notebook: Path) -> dict[str, Any]:
    """Return the keyword arguments of the valid call the definition-error tests override."""
    return {
        "notebook": notebook,
        "executed": _EXECUTED_KEY,
        "registry": registry,
        "env": _ENV,
        "inputs": {"raw_path": "raw"},
        "produces": {"table_path": "tables/out.csv"},
        "parameters": {"location": "179"},
    }


def test_the_base_call_of_the_definition_error_tests_builds(tmp_path: Path) -> None:
    """The base call of test_definition_errors_register_nothing, with no override, returns a Task, so each override's ValueError comes from the override."""
    registry = _registry(tmp_path)
    notebook = _write_notebook(registry.path_for(_SOURCE_KEY), _BASE_PARAMETERS)

    task = notebook_step(**_base_call(registry, notebook))

    assert isinstance(task, Task)


@pytest.mark.parametrize("case", list(_REJECTED))
def test_definition_errors_register_nothing(tmp_path: Path, case: str) -> None:
    """After a ValueError from notebook_step, resolving the executed key or a produces key as an input raises WiringError, because nothing was registered."""
    override = _REJECTED[case]
    registry = _registry(tmp_path)
    # The cell assigns every produced name, so a collision case can raise only through the
    # collision check, never through the parameters-cell check.
    produced: Mapping[str, str] = override.get("produces", {})
    cell = f"{_BASE_PARAMETERS}\n{_assigning(produced)}"
    notebook = _write_notebook(registry.path_for(_SOURCE_KEY), cell)
    call: dict[str, Any] = {**_base_call(registry, notebook), **override}

    executed: str = call["executed"]

    with pytest.raises(ValueError) as info:
        notebook_step(**call)

    message = str(info.value)
    if case == "unassigned_parameter":
        assert "does not assign" in message, message
    else:
        # Each other override fails a check that runs before the parameters-cell check, even
        # where the cell does not assign the overridden name or the source is not a notebook.
        # A parameters-cell message means that check no longer runs first, so this case no
        # longer tests it.
        for fragment in _PARAMETERS_CELL_MESSAGES:
            assert fragment not in message, message
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
    """A produces key equal to the executed key, two produces parameters naming the same key, two produces keys that resolve to one path, or a produces key that resolves to the source notebook raises ValueError naming the step and both colliding items."""
    registry = _registry(tmp_path)
    source = _write_notebook(registry.path_for(_SOURCE_KEY), _assigning(collision.produces))

    with pytest.raises(ValueError) as info:
        notebook_step(
            source,
            executed=_EXECUTED_KEY,
            registry=registry,
            env=_ENV,
            produces=collision.produces,
            name="colliding_report",
        )

    message = str(info.value)
    assert "does not assign" not in message, message
    assert "colliding_report" in message
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


# Wiring errors name the step (MIC-7785)


def test_unknown_input_key_error_names_the_step(tmp_path: Path) -> None:
    """A mistyped input key raises WiringError whose message starts with "Step '<name>': ", names the key, and keeps the did-you-mean, as python_step's does."""
    registry = _registry(tmp_path)
    registry.register_output("tables/summary.csv")

    with pytest.raises(WiringError) as info:
        notebook_step(
            _source(tmp_path),
            executed=_EXECUTED_KEY,
            registry=registry,
            env=_ENV,
            inputs={"summary_path": "tables/sumary.csv"},
            name="summarize",
        )

    message = str(info.value)
    assert message.startswith("Step 'summarize': "), message
    assert "'tables/sumary.csv'" in message
    assert message.endswith("Did you mean 'tables/summary.csv'?"), message


def test_unknown_input_key_error_names_the_default_step_name(tmp_path: Path) -> None:
    """Without name=, the WiringError for a mistyped input key starts with "Step '<executed key>': "."""
    registry = _registry(tmp_path)
    registry.register_output("tables/summary.csv")

    with pytest.raises(WiringError) as info:
        notebook_step(
            _source(tmp_path),
            executed=_EXECUTED_KEY,
            registry=registry,
            env=_ENV,
            inputs={"summary_path": "tables/sumary.csv"},
        )

    message = str(info.value)
    assert message.startswith(f"Step {_EXECUTED_KEY!r}: "), message
    assert "'tables/sumary.csv'" in message


def test_output_on_a_root_input_error_names_the_step(tmp_path: Path) -> None:
    """A produces key that is a declared root input raises WiringError whose message starts with "Step '<name>': " and names the key."""
    with pytest.raises(WiringError) as info:
        notebook_step(
            _source(tmp_path),
            executed=_EXECUTED_KEY,
            registry=_root_input_registry(tmp_path),
            env=_ENV,
            produces={"table_path": _ROOT_TABLE_KEY},
            name="summarize",
        )

    message = str(info.value)
    assert message.startswith("Step 'summarize': "), message
    assert _ROOT_TABLE_KEY in message


# The executed notebook is this run's (MIC-7785)


def test_an_executed_notebook_left_by_an_earlier_run_is_removed_before_the_notebook_runs(
    tmp_path: Path,
) -> None:
    """When an executed notebook from an earlier run is at the executed path and the step's env fails before papermill starts, the step fails and no executed notebook is left at that path."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    source = write_source_notebook(tmp_path / "notebooks" / "stale.ipynb", "table_path = 1")
    task = notebook_step(
        source,
        executed="executed/stale.ipynb",
        registry=registry,
        env=str(broken_env(tmp_path / "broken_env")),
        produces={"table_path": _TABLE_KEY},
        name="stale",
    )
    executed = registry.path_for("executed/stale.ipynb")
    earlier = code_cell("x", execution_count=3, outputs=[error_output("KeyError", "'old'")])
    write_notebook(executed, [earlier])

    assert outcomes(build([task], tmp_path / "state")) == {"stale": TaskOutcome.FAIL}
    assert not executed.exists()


def test_the_command_still_runs_and_succeeds_when_no_executed_notebook_exists(
    tmp_path: Path,
) -> None:
    """A notebook step whose executed notebook has never been written runs and succeeds, so removing a previous one does not fail when there is none."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    source = write_source_notebook(
        tmp_path / "notebooks" / "fresh.ipynb",
        "table_path = 'unset'",
        "from pathlib import Path\nPath(table_path).write_text('n\\n1\\n')",
    )
    task = notebook_step(
        source,
        executed="executed/fresh.ipynb",
        registry=registry,
        env=sys.prefix,
        produces={"table_path": _TABLE_KEY},
        name="fresh",
    )
    executed = registry.path_for("executed/fresh.ipynb")
    assert not executed.exists()

    assert outcomes(build([task], tmp_path / "state")) == {"fresh": TaskOutcome.SUCCESS}
    assert executed.is_file()
    assert registry.path_for(_TABLE_KEY).read_text() == "n\n1\n"


# describe_notebook_failure (MIC-7785)

_KEY_ERROR = error_output("KeyError", "'no_such_column'")
"""The error output Jupyter saves for ``{}['no_such_column']``."""


class _RemoteFailure(Exception):
    """Stand in for a failure reported from another process, which names the remote error."""

    def __init__(self, exc_type: str, exc_message: str) -> None:
        super().__init__("a step failed in another process")
        self.exc_type = exc_type
        self.exc_message = exc_message


@dataclass
class _FailedStep:
    """A notebook step 'summarize' that is compiled but never built, and its files."""

    task: Task
    source: Path
    executed: Path

    @property
    def command(self) -> str:
        """Return the step's generated command."""
        assert isinstance(self.task.run, str)
        return self.task.run

    @property
    def called(self) -> subprocess.CalledProcessError:
        """Return the error the step's command fails with when it exits 1."""
        return subprocess.CalledProcessError(1, self.command)

    def described(self, error: BaseException | None = None) -> str | None:
        """Describe the step's failure with ``error``, by default ``called``."""
        return describe_notebook_failure(self.task, self.called if error is None else error)

    def raised(self, error: str, cell: str = " in cell In [3]") -> str:
        """Return the description of ``error`` raised in ``cell``."""
        return (
            f"notebook {self.source.name} raised {error}{cell}. The executed notebook, "
            f"with the error, is at {self.executed}."
        )

    def fallback(self, returncode: int = 1) -> str:
        """Return the description of an exit with ``returncode`` and no recorded error."""
        return (
            f"notebook {self.source.name} left no error in an executed notebook at "
            f"{self.executed}, and the command exited with status {returncode}; its output "
            "is in pytask's report for the step."
        )


def _failed_step(tmp_path: Path) -> _FailedStep:
    """Compile a notebook step 'summarize' whose executed notebook is not yet written."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    source = write_source_notebook(
        tmp_path / "notebooks" / "summarize.ipynb", "table_path = 'unset'"
    )
    task = notebook_step(
        source,
        executed="executed/summarize.ipynb",
        registry=registry,
        env=_ENV,
        produces={"table_path": _TABLE_KEY},
        name="summarize",
    )
    return _FailedStep(task, source, registry.path_for("executed/summarize.ipynb"))


def _executed_cells(*error_cells: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a parameters cell, an injected-parameters cell, then ``error_cells``."""
    return [
        code_cell("table_path = 'unset'", tags=["parameters"], execution_count=1),
        code_cell("table_path = '/x'", tags=["injected-parameters"], execution_count=2),
        *error_cells,
    ]


def test_describe_notebook_failure_names_the_notebook_error_cell_and_executed_path(
    tmp_path: Path,
) -> None:
    """For a notebook step whose executed notebook has a KeyError output in a cell with execution count 3, the description is exactly "notebook <source name> raised KeyError: 'no_such_column' in cell In [3]. The executed notebook, with the error, is at <absolute executed path>."."""
    step = _failed_step(tmp_path)
    failing = code_cell("{}['no_such_column']", execution_count=3, outputs=[_KEY_ERROR])
    write_notebook(step.executed, _executed_cells(failing))

    description = step.described()

    assert step.executed.is_absolute()
    assert description == (
        "notebook summarize.ipynb raised KeyError: 'no_such_column' in cell In [3]. The "
        f"executed notebook, with the error, is at {step.executed}."
    )


def test_describe_notebook_failure_reads_the_notebook_whatever_the_error_type(
    tmp_path: Path,
) -> None:
    """The description from an executed notebook with an error output is the same whether the error passed is a CalledProcessError, a RuntimeError, or an object carrying exc_type and exc_message as a remote failure does."""
    step = _failed_step(tmp_path)
    failing = code_cell("{}['no_such_column']", execution_count=3, outputs=[_KEY_ERROR])
    write_notebook(step.executed, _executed_cells(failing))
    errors: list[BaseException] = [
        step.called,
        RuntimeError("the worker failed"),
        _RemoteFailure("CalledProcessError", "Command 'python -m papermill' failed."),
    ]

    descriptions = [step.described(error) for error in errors]

    expected = step.raised("KeyError: 'no_such_column'")
    assert descriptions == [expected] * len(errors)


def test_describe_notebook_failure_leaves_out_an_empty_error_value(tmp_path: Path) -> None:
    """An error output whose evalue is empty is described as 'raised <ename> in cell In [<n>].', with no colon after the name."""
    step = _failed_step(tmp_path)
    failing = code_cell(
        "raise StopIteration", execution_count=3, outputs=[error_output("StopIteration", "")]
    )
    write_notebook(step.executed, _executed_cells(failing))

    description = step.described()

    assert description == step.raised("StopIteration")
    assert description is not None and "StopIteration:" not in description


def test_describe_notebook_failure_gives_only_the_first_non_blank_line_of_a_multi_line_error_value(
    tmp_path: Path,
) -> None:
    """An evalue with a blank first line and several more lines is described by its first non-blank line, stripped, and the description has no newline."""
    step = _failed_step(tmp_path)
    value = " \t\n\n  column 'deaths' is missing  \n  from the table\nsee the docs\n"
    failing = code_cell(
        "check(table)", execution_count=3, outputs=[error_output("ValueError", value)]
    )
    write_notebook(step.executed, _executed_cells(failing))

    description = step.described()

    assert description == step.raised("ValueError: column 'deaths' is missing")
    assert description is not None and "\n" not in description


def test_describe_notebook_failure_leaves_out_an_error_value_with_only_blank_lines(
    tmp_path: Path,
) -> None:
    """An evalue whose lines are all blank is left out, as an empty one is."""
    step = _failed_step(tmp_path)
    failing = code_cell(
        "raise StopIteration",
        execution_count=3,
        outputs=[error_output("StopIteration", " \n\t\n")],
    )
    write_notebook(step.executed, _executed_cells(failing))

    description = step.described()

    assert description == step.raised("StopIteration")
    assert description is not None and "StopIteration:" not in description


def test_describe_notebook_failure_leaves_out_the_cell_when_it_has_no_execution_count(
    tmp_path: Path,
) -> None:
    """An error output in a cell whose execution_count is null is described without ' in cell In [...]'."""
    step = _failed_step(tmp_path)
    failing = code_cell("{}['no_such_column']", execution_count=None, outputs=[_KEY_ERROR])
    write_notebook(step.executed, _executed_cells(failing))

    description = step.described()

    assert description == step.raised("KeyError: 'no_such_column'", cell="")
    assert description is not None and "In [" not in description


def test_describe_notebook_failure_reports_the_first_error_in_cell_order(
    tmp_path: Path,
) -> None:
    """When two cells hold error outputs, the description names the first cell's error and execution count."""
    step = _failed_step(tmp_path)
    # The first cell in order ran last, so cell order and execution order disagree.
    first = code_cell(
        "raise ValueError('first')",
        execution_count=5,
        outputs=[error_output("ValueError", "first")],
    )
    second = code_cell(
        "raise TypeError('second')",
        execution_count=4,
        outputs=[error_output("TypeError", "second")],
    )
    write_notebook(step.executed, _executed_cells(first, second))

    assert step.described() == step.raised("ValueError: first", cell=" in cell In [5]")


def test_describe_notebook_failure_ignores_stream_and_display_outputs(tmp_path: Path) -> None:
    """Stream and display outputs before the failing cell do not change the description, which names only the error output."""
    step = _failed_step(tmp_path)
    stream: dict[str, Any] = {"output_type": "stream", "name": "stdout", "text": ["rows\n"]}
    display: dict[str, Any] = {
        "output_type": "display_data",
        "data": {"text/plain": ["42"]},
        "metadata": {},
    }
    shown: dict[str, Any] = {
        "output_type": "execute_result",
        "execution_count": 3,
        "data": {"text/plain": ["7"]},
        "metadata": {},
    }
    printing = code_cell("print('3 rows')\n7", execution_count=3, outputs=[stream, shown])
    failing = code_cell(
        "{}['no_such_column']", execution_count=4, outputs=[display, stream, _KEY_ERROR]
    )
    write_notebook(step.executed, _executed_cells(printing, failing))

    description = step.described()

    assert description == step.raised("KeyError: 'no_such_column'", cell=" in cell In [4]")


def test_describe_notebook_failure_falls_back_to_the_exit_status_without_an_executed_notebook(
    tmp_path: Path,
) -> None:
    """With no file at the executed path and a CalledProcessError with return code 1, the description is exactly "notebook <source name> left no error in an executed notebook at <path>, and the command exited with status 1; its output is in pytask's report for the step."."""
    step = _failed_step(tmp_path)
    assert not step.executed.exists()

    description = step.described()

    assert description == (
        "notebook summarize.ipynb left no error in an executed notebook at "
        f"{step.executed}, and the command exited with status 1; its output is in "
        "pytask's report for the step."
    )


def test_describe_notebook_failure_falls_back_when_the_executed_notebook_is_not_json(
    tmp_path: Path,
) -> None:
    """An executed notebook that is truncated or not JSON gives the exit-status fallback, not an exception."""
    step = _failed_step(tmp_path)
    write_notebook(step.executed, _executed_cells())
    complete = step.executed.read_text(encoding="utf-8")
    contents = [complete[: len(complete) // 2], "this is not JSON", ""]

    for content in contents:
        step.executed.write_text(content, encoding="utf-8")
        assert step.described() == step.fallback(), content


def test_describe_notebook_failure_falls_back_for_json_that_is_not_a_notebook(
    tmp_path: Path,
) -> None:
    """Valid JSON that is not a notebook, such as a list or a notebook whose cells lack outputs, gives the exit-status fallback, not an exception."""
    step = _failed_step(tmp_path)
    contents: list[object] = [
        [],
        {},
        "a notebook",
        {"cells": [{"cell_type": "code", "source": "x", "execution_count": 3}]},
        {"cells": [{"cell_type": "markdown", "source": "# Summary"}]},
    ]

    for content in contents:
        step.executed.parent.mkdir(parents=True, exist_ok=True)
        step.executed.write_text(json.dumps(content), encoding="utf-8")
        assert step.described() == step.fallback(), content


def test_describe_notebook_failure_falls_back_when_the_executed_notebook_records_no_error(
    tmp_path: Path,
) -> None:
    """An executed notebook without an error output and a CalledProcessError give the exit-status fallback."""
    step = _failed_step(tmp_path)
    stream: dict[str, Any] = {"output_type": "stream", "name": "stdout", "text": ["done\n"]}
    body = code_cell("print('done')", execution_count=3, outputs=[stream])
    write_notebook(step.executed, _executed_cells(body))

    assert step.described(subprocess.CalledProcessError(2, step.command)) == step.fallback(2)


def test_describe_notebook_failure_fallback_does_not_contain_the_generated_command(
    tmp_path: Path,
) -> None:
    """The fallback for a CalledProcessError whose cmd is the step's papermill command contains neither 'papermill' nor the command text."""
    step = _failed_step(tmp_path)
    assert "papermill" in step.command

    description = step.described()

    assert description == step.fallback()
    assert description is not None
    assert "papermill" not in description
    assert step.command not in description


def test_describe_notebook_failure_is_none_for_a_task_that_is_not_a_notebook_step(
    tmp_path: Path,
) -> None:
    """A python_step task and a hand-built command task, each with a CalledProcessError, are described as None."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    python_task = python_step(
        _offset_table, inputs={}, produces="offset.parquet", registry=registry
    )
    command_task = Task(
        name="shout",
        run="exit 1",
        inputs={},
        outputs={"out": registry.register_output("shout.txt")},
        resources={},
        env=None,
    )

    for task in (python_task, command_task):
        error = subprocess.CalledProcessError(1, "exit 1")
        assert describe_notebook_failure(task, error) is None, task.name


def test_describe_notebook_failure_is_none_without_a_recorded_error_or_a_called_process_error(
    tmp_path: Path,
) -> None:
    """A notebook step whose executed notebook records no error, failing with NodeNotFoundError as when a product was not written, is described as None."""
    step = _failed_step(tmp_path)
    write_notebook(step.executed, _executed_cells(code_cell("pass", execution_count=3)))
    error = NodeNotFoundError(f"{step.task.name}: a declared product was not written.")

    assert step.described(error) is None


def test_describe_notebook_failure_reads_a_real_failing_notebook(tmp_path: Path) -> None:
    """After a build in which a notebook's body cell raises KeyError, describe_notebook_failure with the step's CalledProcessError names the source notebook, 'KeyError', the cell's execution count as papermill numbered it, and the executed notebook's path."""
    registry = ArtifactRegistry(tmp_path / "out", "run1")
    source = write_source_notebook(
        tmp_path / "notebooks" / "summarize.ipynb",
        "table_path = 'unset'",
        "{}['no_such_column']",
    )
    task = notebook_step(
        source,
        executed="executed/summarize.ipynb",
        registry=registry,
        env=sys.prefix,
        produces={"table_path": _TABLE_KEY},
        name="summarize",
    )
    executed = registry.path_for("executed/summarize.ipynb")

    session = build([task], tmp_path / "state")

    [report] = session.execution_reports
    assert report.exc_info is not None
    error = report.exc_info[1]
    assert isinstance(error, subprocess.CalledProcessError)
    assert ("KeyError", "'no_such_column'") in recorded_errors(executed)
    # Papermill numbers the parameters cell 1 and the cell it injects 2, so the body is 3.
    assert describe_notebook_failure(task, error) == (
        "notebook summarize.ipynb raised KeyError: 'no_such_column' in cell In [3]. The "
        f"executed notebook, with the error, is at {executed}."
    )


# The parameters cell check (MIC-7755)

_COUNTED_FORMS: dict[str, str] = {
    "plain": "location = 1",
    "chained": "other = location = 1",
    "tuple_unpacking": "location, other = 1, 2",
    "nested_unpacking": "(location, other), third = (1, 2), 3",
    "starred_list_unpacking": "[first, *location] = [1, 2, 3]",
    "annotated_with_value": "location: int = 1",
    "annotated_without_value": "location: int",
}
"""Parameters cells that each assign 'location' at their top level, keyed by form."""

_INDIRECT_FORMS: dict[str, str] = {
    "for_target": "for location in range(1):\n    pass",
    "inside_for": "for _ in range(1):\n    location = 1",
    "inside_while": "while True:\n    location = 1\n    break",
    "inside_if": "if True:\n    location = 1",
    "inside_with": "with context():\n    location = 1",
    "with_target": "with context() as location:\n    pass",
    "inside_try": "try:\n    location = 1\nexcept Exception:\n    pass",
    "inside_function": "def define():\n    location = 1",
    "inside_class": "class Holder:\n    location = 1",
    "globals": 'globals()["location"] = 1',
    "exec": 'exec("location = 1")',
    "setattr": 'setattr(module, "location", 1)',
    "augmented": "location += 1",
    "walrus": "(location := 1)",
    "import": "import location",
    "import_from": "from package import location",
    "function_definition": "def location():\n    pass",
    "class_definition": "class location:\n    pass",
    "attribute_target": "config.location = 1",
    "subscript_target": 'config["location"] = 1',
    "annotated_attribute_target": "config.location: int = 1",
}
"""Parameters cells that bind 'location' only in a way the check does not count, keyed by form."""

_FIFTEEN_NAMES = tuple(f"unknown_{index:02d}" for index in range(15))
"""Fifteen names in sorted order, none close to a name the tests' parameters cells assign."""


def _checked_source(tmp_path: Path, parameters: str) -> Path:
    """Write a source notebook whose parameters cell holds ``parameters``."""
    return _write_notebook(tmp_path / "notebooks" / "report.ipynb", parameters)


def _cells_source(tmp_path: Path, cells: list[dict[str, Any]]) -> Path:
    """Write a source notebook holding ``cells``."""
    return write_notebook(tmp_path / "notebooks" / "report.ipynb", cells)


def _written(path: Path, content: _NotebookContent) -> Path:
    """Write ``content`` at ``path``: cells through write_notebook, text or bytes as is."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    elif isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        write_notebook(path, content)
    return path


def _step(tmp_path: Path, source: Path, **arguments: Any) -> Task:
    """Compile a step over ``source`` with ``arguments``, named _NOTEBOOK_STEP by default."""
    call: dict[str, Any] = {"name": _NOTEBOOK_STEP, **arguments}
    return notebook_step(
        source, executed=_EXECUTED_KEY, registry=_registry(tmp_path), env=_ENV, **call
    )


def _rejection(tmp_path: Path, source: Path, **arguments: Any) -> str:
    """Return the message of the ValueError that ``_step`` with ``arguments`` raises."""
    with pytest.raises(ValueError) as info:
        _step(tmp_path, source, **arguments)
    return str(info.value)


def _syntax_error(text: str) -> SyntaxError:
    """Return the SyntaxError that parsing ``text`` raises."""
    try:
        ast.parse(text)
    except SyntaxError as error:
        return error
    raise AssertionError(f"{text!r} is valid Python")


def _mncnh_cells(*first: dict[str, Any]) -> list[dict[str, Any]]:
    """Return ``first``, then cells shaped like MNCNH's hemorrhage_split_proportions.ipynb."""
    return [
        *first,
        text_cell("markdown", "# Hemorrhage split proportions"),
        code_cell("from pathlib import Path\n\nimport pandas as pd"),
        code_cell("model_spec = {'artifact_path': '/model/artifacts'}"),
        code_cell("artifact = Path(model_spec['artifact_path']) / 'artifact.hdf'"),
        code_cell("proportions = pd.read_hdf(artifact, 'hemorrhage/split')"),
    ]


# Names the cell does not assign


def test_a_misspelled_parameter_name_is_rejected_with_a_did_you_mean(tmp_path: Path) -> None:
    """parameters={'loaction_id': '179'} over a cell assigning 'location_id' raises ValueError starting "Step '<name>': " and containing repr(str(source)) and "'loaction_id' (parameters; did you mean 'location_id'?)"."""
    source = _checked_source(tmp_path, "location_id = None")

    message = _rejection(tmp_path, source, parameters={"loaction_id": "179"})

    assert message.startswith(f"Step '{_NOTEBOOK_STEP}': "), message
    assert repr(str(source.resolve())) in message
    assert "'loaction_id' (parameters; did you mean 'location_id'?)" in message


def test_a_misspelled_input_name_is_rejected_with_a_did_you_mean(tmp_path: Path) -> None:
    """inputs={'raw_pth': 'raw'} over a cell assigning 'raw_path' raises ValueError starting "Step '<name>': " and containing repr(str(source)) and "'raw_pth' (inputs; did you mean 'raw_path'?)"."""
    source = _checked_source(tmp_path, "raw_path = None")

    message = _rejection(tmp_path, source, inputs={"raw_pth": "raw"})

    assert message.startswith(f"Step '{_NOTEBOOK_STEP}': "), message
    assert repr(str(source.resolve())) in message
    assert "'raw_pth' (inputs; did you mean 'raw_path'?)" in message


def test_a_misspelled_product_name_is_rejected_with_a_did_you_mean(tmp_path: Path) -> None:
    """produces={'table_pth': 'tables/out.csv'} over a cell assigning 'table_path' raises ValueError starting "Step '<name>': " and containing repr(str(source)) and "'table_pth' (produces; did you mean 'table_path'?)"."""
    source = _checked_source(tmp_path, "table_path = None")

    message = _rejection(tmp_path, source, produces={"table_pth": "tables/out.csv"})

    assert message.startswith(f"Step '{_NOTEBOOK_STEP}': "), message
    assert repr(str(source.resolve())) in message
    assert "'table_pth' (produces; did you mean 'table_path'?)" in message


def test_the_unassigned_names_message_gives_each_name_its_argument_and_the_fix(
    tmp_path: Path,
) -> None:
    """A step 'report' passing parameters={'loaction_id': '179'} and inputs={'raw_path': 'raw'} over a cell assigning 'location_id' and 'draws' raises ValueError whose message is exactly "Step 'report': the parameters cell of notebook <repr(str(source))> does not assign 'loaction_id' (parameters; did you mean 'location_id'?), 'raw_path' (inputs). papermill would inject each one as a variable the notebook never reads. Fix the name in the step, or assign it in that cell with a plain top-level assignment such as loaction_id = None; assignments in loops, conditions or through globals() are not counted."."""
    source = _checked_source(tmp_path, "location_id = None\ndraws = None")

    message = _rejection(
        tmp_path,
        source,
        name="report",
        parameters={"loaction_id": "179"},
        inputs={"raw_path": "raw"},
    )

    assert message == (
        f"Step 'report': the parameters cell of notebook {str(source.resolve())!r} does "
        "not assign 'loaction_id' (parameters; did you mean 'location_id'?), 'raw_path' "
        "(inputs). papermill would inject each one as a variable the notebook never reads. "
        "Fix the name in the step, or assign it in that cell with a plain top-level "
        "assignment such as loaction_id = None; assignments in loops, conditions or "
        "through globals() are not counted."
    )


def test_an_unassigned_name_with_no_close_match_has_no_suggestion(tmp_path: Path) -> None:
    """A passed name unlike every name the cell assigns is listed as "'<name>' (<argument>)" and the message has no "did you mean"."""
    source = _checked_source(tmp_path, "location = None\ndraws = None")

    message = _rejection(tmp_path, source, parameters={"zzz": 1})

    assert "'zzz' (parameters)" in message, message
    assert "did you mean" not in message.lower(), message


def test_more_than_ten_unassigned_names_list_ten_in_sorted_order_and_count_the_rest(
    tmp_path: Path,
) -> None:
    """Fifteen unassigned names, passed in reverse order, are listed as the first ten in sorted order followed by ", and 5 more", the other five are not named, and the fix hint uses the first sorted name ("such as <first> = None")."""
    source = _checked_source(tmp_path, "location = None")
    parameters = {name: 1 for name in reversed(_FIFTEEN_NAMES)}

    message = _rejection(tmp_path, source, parameters=parameters)

    listed = ", ".join(f"'{name}' (parameters)" for name in _FIFTEEN_NAMES[:10])
    assert f"{listed}, and 5 more" in message, message
    assert [name for name in _FIFTEEN_NAMES[10:] if f"'{name}'" in message] == []
    assert f"such as {_FIFTEEN_NAMES[0]} = None" in message, message


def test_names_the_cell_assigns_but_the_step_does_not_pass_are_allowed(
    tmp_path: Path,
) -> None:
    """A cell that assigns names the step does not pass, beside every name it does pass, compiles to a Task."""
    source = _checked_source(tmp_path, "location = None\ndraws = 10\nverbose = False")

    task = _step(tmp_path, source, parameters={"location": "179"})

    assert isinstance(task, Task)


def test_a_notebook_whose_cell_assigns_every_passed_name_builds(tmp_path: Path) -> None:
    """A step passing names in inputs, produces and parameters over a cell that assigns each one compiles to a Task with every input and product wired."""
    source = _checked_source(tmp_path, _BASE_PARAMETERS)

    task = _step(
        tmp_path,
        source,
        inputs={"raw_path": "raw"},
        produces={"table_path": "tables/out.csv"},
        parameters={"location": "179"},
    )

    assert isinstance(task, Task)
    assert set(task.inputs) == {"__notebook__", "raw_path"}
    assert set(task.outputs) == {"__executed__", "table_path"}


# A notebook with no parameters cell


def test_a_notebook_with_no_parameters_cell_is_rejected_when_the_step_passes_names(
    tmp_path: Path,
) -> None:
    """A step 'split' passing inputs={'artifact': 'raw'} over a notebook with no cell tagged 'parameters' raises ValueError whose message is exactly "Step 'split': notebook <repr(str(source))> has no cell tagged 'parameters', so papermill would inject 'artifact' at the top of the notebook, where a later cell can overwrite what it injects. Add a code cell tagged 'parameters' that assigns each name, such as artifact = None."."""
    source = _cells_source(tmp_path, [code_cell("print('no parameters here')")])

    message = _rejection(tmp_path, source, name="split", inputs={"artifact": "raw"})

    assert message == (
        f"Step 'split': notebook {str(source.resolve())!r} has no cell tagged 'parameters', "
        "so papermill would inject 'artifact' at the top of the notebook, where a later "
        "cell can overwrite what it injects. Add a code cell tagged 'parameters' that "
        "assigns each name, such as artifact = None."
    )


def test_more_than_ten_names_to_a_notebook_with_no_parameters_cell_list_ten_and_count_the_rest(
    tmp_path: Path,
) -> None:
    """Fifteen names passed to a notebook with no parameters cell are listed as the first ten in sorted order followed by ", and 5 more", and the fix hint uses the first sorted name."""
    source = _cells_source(tmp_path, [code_cell("location = None")])
    parameters = {name: 1 for name in reversed(_FIFTEEN_NAMES)}

    message = _rejection(tmp_path, source, parameters=parameters)

    assert "has no cell tagged 'parameters'" in message, message
    listed = ", ".join(f"'{name}'" for name in _FIFTEEN_NAMES[:10])
    assert f"{listed}, and 5 more" in message, message
    assert [name for name in _FIFTEEN_NAMES[10:] if f"'{name}'" in message] == []
    assert f"such as {_FIFTEEN_NAMES[0]} = None" in message, message


def test_a_notebook_with_no_parameters_cell_builds_when_the_step_passes_no_names(
    tmp_path: Path,
) -> None:
    """A step with no inputs, produces or parameters over a notebook with no cell tagged 'parameters' compiles to a Task."""
    source = _cells_source(tmp_path, [code_cell("location = None"), code_cell("pass")])

    task = _step(tmp_path, source)

    assert isinstance(task, Task)


def test_an_mncnh_shaped_notebook_without_a_parameters_cell_is_rejected(
    tmp_path: Path,
) -> None:
    """A notebook shaped like MNCNH's hemorrhage_split_proportions.ipynb (no parameters cell, a later cell that assigns 'artifact'), passed inputs={'artifact': ...}, raises ValueError with the no-parameters-cell message naming 'artifact'."""
    source = _cells_source(tmp_path, _mncnh_cells())

    message = _rejection(
        tmp_path, source, name="split_proportions_notebook", inputs={"artifact": "raw"}
    )

    assert message.startswith("Step 'split_proportions_notebook': "), message
    assert repr(str(source.resolve())) in message
    assert "has no cell tagged 'parameters'" in message, message
    assert "would inject 'artifact' at the top of the notebook" in message, message
    assert "such as artifact = None" in message, message


def test_the_mncnh_shaped_notebook_with_a_parameters_cell_assigning_artifact_builds(
    tmp_path: Path,
) -> None:
    """The same MNCNH-shaped notebook with a first cell tagged 'parameters' holding 'artifact = None', passed inputs={'artifact': ...}, compiles to a Task."""
    cells = _mncnh_cells(code_cell("artifact = None", tags=["parameters"]))
    source = _cells_source(tmp_path, cells)

    task = _step(
        tmp_path, source, name="split_proportions_notebook", inputs={"artifact": "raw"}
    )

    assert isinstance(task, Task)
    assert set(task.inputs) == {"__notebook__", "artifact"}


# A step that passes no names does not read the notebook


def test_a_step_with_no_names_over_a_file_that_is_not_json_compiles(tmp_path: Path) -> None:
    """A step with no inputs, produces or parameters over an .ipynb file that is not JSON compiles to a Task."""
    source = _written(tmp_path / "notebooks" / "report.ipynb", "this is not JSON {")

    task = _step(tmp_path, source)

    assert isinstance(task, Task)


def test_a_step_with_no_names_over_json_with_no_cells_list_compiles(tmp_path: Path) -> None:
    """A step with no inputs, produces or parameters over an .ipynb file holding JSON with no top-level 'cells' list (an nbformat 3 shape) compiles to a Task."""
    content = _UNREADABLE_NOTEBOOKS["nbformat_3_worksheets"]
    source = _written(tmp_path / "notebooks" / "report.ipynb", content)

    task = _step(tmp_path, source)

    assert isinstance(task, Task)


# Which assignments count


@pytest.mark.parametrize("cell", list(_COUNTED_FORMS.values()), ids=list(_COUNTED_FORMS))
def test_each_counted_assignment_form_assigns_the_name(tmp_path: Path, cell: str) -> None:
    """A parameters cell that assigns 'location' by a plain, chained, unpacking (tuple, nested, or list with a starred target) or annotated assignment (with or without a value) lets a step pass parameters={'location': '179'}."""
    source = _checked_source(tmp_path, cell)

    task = _step(tmp_path, source, parameters={"location": "179"})

    assert isinstance(task, Task)


@pytest.mark.parametrize("cell", list(_INDIRECT_FORMS.values()), ids=list(_INDIRECT_FORMS))
def test_each_indirect_binding_is_reported_as_not_assigned(tmp_path: Path, cell: str) -> None:
    """A parameters cell that binds 'location' only inside a loop, condition, with, try, function or class, through globals(), exec or setattr, by augmented assignment, walrus, import, def or class, or as an attribute or subscript target, makes a step passing parameters={'location': '179'} raise ValueError naming "'location' (parameters".

    The cell is only parsed, never run, so names such as context and module are not defined.
    """
    source = _checked_source(tmp_path, cell)

    message = _rejection(tmp_path, source, parameters={"location": "179"})

    assert "'location' (parameters" in message, message


@pytest.mark.parametrize("form", ["string", "list"])
def test_cell_source_may_be_one_string_or_a_list_of_strings(
    tmp_path: Path, form: str
) -> None:
    """A parameters cell whose source is one string, or a list of strings that join with "" into assignments split across entries, lets a step pass the names it assigns."""
    # Joined with anything but "", these pieces are not valid Python.
    pieces = ["loca", "tion = None\n", "draws = ", "None"]
    cell = code_cell("".join(pieces), tags=["parameters"])
    if form == "list":
        cell["source"] = pieces
    source = _cells_source(tmp_path, [cell, code_cell("pass")])

    task = _step(tmp_path, source, parameters={"location": "179", "draws": 3})

    assert isinstance(task, Task)


def test_names_assigned_outside_the_parameters_cell_do_not_count(tmp_path: Path) -> None:
    """A name assigned only in an untagged code cell before or after the parameters cell is reported as not assigned."""
    cells = [
        code_cell("before = None"),
        code_cell("location = None", tags=["parameters"]),
        code_cell("after = None"),
    ]
    source = _cells_source(tmp_path, cells)
    parameters: dict[str, NotebookParameter] = {"location": "179", "before": 1, "after": 2}

    message = _rejection(tmp_path, source, parameters=parameters)

    assert "'before' (parameters" in message, message
    assert "'after' (parameters" in message, message
    assert "'location' (" not in message, message


def test_only_the_first_cell_tagged_parameters_counts(tmp_path: Path) -> None:
    """A name assigned only in a second cell tagged 'parameters' is reported as not assigned, and a name the first tagged cell assigns is accepted."""
    cells = [
        code_cell("location = None", tags=["parameters"]),
        code_cell("draws = None", tags=["parameters"]),
    ]
    source = _cells_source(tmp_path, cells)

    message = _rejection(tmp_path, source, parameters={"location": "179", "draws": 3})
    task = _step(tmp_path, source, parameters={"location": "179"})

    assert "'draws' (parameters" in message, message
    assert "'location' (" not in message, message
    assert isinstance(task, Task)


def test_a_cell_tagged_injected_parameters_does_not_count(tmp_path: Path) -> None:
    """In a notebook papermill has already executed, a name assigned only in the cell tagged 'injected-parameters' is reported as not assigned."""
    # The injected cell comes first, so a tag rule that accepted 'injected-parameters' would
    # read it as the parameters cell and find 'location' assigned.
    cells = [
        code_cell("location = '179'", tags=["injected-parameters"], execution_count=1),
        code_cell("draws = None", tags=["parameters"], execution_count=2),
        code_cell("pass", execution_count=3),
    ]
    source = _cells_source(tmp_path, cells)

    message = _rejection(tmp_path, source, parameters={"location": "179", "draws": 3})

    assert "'location' (parameters" in message, message
    assert "'draws' (" not in message, message


def test_cells_with_no_metadata_or_no_tags_are_not_the_parameters_cell(
    tmp_path: Path,
) -> None:
    """Cells with no 'metadata' key, or with metadata but no 'tags', before the parameters cell are skipped without error, and the step compiles."""
    no_metadata = {
        key: value for key, value in code_cell("first = 1").items() if key != "metadata"
    }
    no_tags = {**code_cell("second = 2"), "metadata": {"collapsed": True}}
    empty_metadata = code_cell("third = 3")
    assert empty_metadata["metadata"] == {}
    cells = [
        no_metadata,
        no_tags,
        empty_metadata,
        code_cell("location = None", tags=["parameters"]),
    ]
    source = _cells_source(tmp_path, cells)

    task = _step(tmp_path, source, parameters={"location": "179"})

    assert isinstance(task, Task)


@pytest.mark.parametrize("cell_type", ["markdown", "raw"])
def test_a_first_parameters_cell_that_is_not_code_is_rejected(
    tmp_path: Path, cell_type: str
) -> None:
    """A first cell tagged 'parameters' that is a markdown or raw cell raises ValueError naming the step and repr(str(source)) and saying "must be a code cell", even when a later cell tagged 'parameters' is a code cell assigning every passed name."""
    kind = cast(Literal["markdown", "raw"], cell_type)
    cells = [
        text_cell(kind, "location = None", tags=["parameters"]),
        code_cell("location = None", tags=["parameters"]),
    ]
    source = _cells_source(tmp_path, cells)

    message = _rejection(tmp_path, source, parameters={"location": "179"})

    assert message.startswith(f"Step '{_NOTEBOOK_STEP}': "), message
    assert repr(str(source.resolve())) in message
    assert "must be a code cell" in message, message


@pytest.mark.parametrize(
    "line", ["%matplotlib inline", "!ls", "location = ("], ids=["magic", "shell", "unclosed"]
)
def test_a_parameters_cell_that_is_not_valid_python_is_rejected(
    tmp_path: Path, line: str
) -> None:
    """A parameters cell holding 'location = None' then ``line`` raises ValueError naming the step, repr(str(source)), "is not valid Python", "line 2" and the SyntaxError's message, and saying to keep only plain assignments in that cell."""
    cell = f"location = None\n{line}"
    error = _syntax_error(cell)
    assert error.lineno == 2
    source = _checked_source(tmp_path, cell)

    message = _rejection(tmp_path, source, parameters={"location": "179"})

    assert message.startswith(f"Step '{_NOTEBOOK_STEP}': "), message
    assert repr(str(source.resolve())) in message
    assert "is not valid Python" in message, message
    assert "line 2" in message, message
    assert error.msg in message, message
    assert "plain assignments" in message, message


def test_a_parameters_cell_with_an_invalid_escape_builds_when_its_warning_is_an_error(
    tmp_path: Path,
) -> None:
    r"""A parameters cell 'location = None' then 'path = "C:\data"', whose invalid escape sequence warns when parsed, lets a step passing location and path compile to a Task while that warning is an error."""
    cell = 'location = None\npath = "C:\\data"'
    source = _checked_source(tmp_path, cell)

    with warnings.catch_warnings():
        # Python 3.11 warns with DeprecationWarning and 3.12 with SyntaxWarning; the message
        # filter covers both without failing on unrelated warnings.
        warnings.filterwarnings("error", message="invalid escape sequence")
        assert "invalid escape sequence" in _syntax_error(cell).msg
        task = _step(tmp_path, source, parameters={"location": "179", "path": "x"})

    assert isinstance(task, Task)


# A notebook that cannot be read


@pytest.mark.parametrize(
    "content", list(_REJECTED_NOTEBOOKS.values()), ids=list(_REJECTED_NOTEBOOKS)
)
def test_definition_errors_from_the_notebook_file_register_nothing(
    tmp_path: Path, content: _NotebookContent
) -> None:
    """With ``content`` written at path_for(_SOURCE_KEY) (cells through write_notebook, text or bytes as is), the base call raises ValueError, and then neither the executed key nor 'tables/out.csv' resolves as an input."""
    registry = _registry(tmp_path)
    notebook = _written(registry.path_for(_SOURCE_KEY), content)

    with pytest.raises(ValueError):
        notebook_step(**_base_call(registry, notebook))

    for key in (_EXECUTED_KEY, "tables/out.csv"):
        with pytest.raises(WiringError):
            registry.resolve_input(key)


@pytest.mark.parametrize(
    "content", list(_UNREADABLE_NOTEBOOKS.values()), ids=list(_UNREADABLE_NOTEBOOKS)
)
def test_a_notebook_that_cannot_be_read_is_rejected_naming_the_step_and_notebook(
    tmp_path: Path, content: _NotebookContent
) -> None:
    """With ``content`` written at the source path, the base call raises ValueError (never KeyError, TypeError, AttributeError or a bare UnicodeDecodeError) whose message holds "Step '<name>': ", repr(str(source)) and "cannot be read as a notebook"."""
    registry = _registry(tmp_path)
    notebook = _written(registry.path_for(_SOURCE_KEY), content)
    call = {**_base_call(registry, notebook), "name": _NOTEBOOK_STEP}

    with pytest.raises(ValueError) as info:
        notebook_step(**call)

    assert not isinstance(info.value, UnicodeDecodeError), repr(info.value)
    message = str(info.value)
    assert message.startswith(f"Step '{_NOTEBOOK_STEP}': "), message
    assert repr(str(notebook.resolve())) in message, message
    assert "cannot be read as a notebook" in message, message


def test_a_malformed_cell_in_a_notebook_with_no_parameters_cell_is_reported_as_unreadable(
    tmp_path: Path,
) -> None:
    """A notebook with no cell tagged 'parameters' and one cells entry that is not an object raises the "cannot be read as a notebook" ValueError, not the no-parameters-cell one."""
    registry = _registry(tmp_path)
    content = _notebook_text([code_cell(_BASE_PARAMETERS), "not a cell", code_cell("pass")])
    notebook = _written(registry.path_for(_SOURCE_KEY), content)

    with pytest.raises(ValueError) as info:
        notebook_step(**_base_call(registry, notebook))

    message = str(info.value)
    assert "cannot be read as a notebook" in message, message
    assert "has no cell tagged" not in message, message


def test_a_cells_entry_that_is_not_an_object_after_the_parameters_cell_is_not_read(
    tmp_path: Path,
) -> None:
    """A valid parameters cell followed by a cells entry that is not an object compiles to a Task."""
    registry = _registry(tmp_path)
    parameters = code_cell(_BASE_PARAMETERS, tags=["parameters"])
    content = _notebook_text([parameters, "not a cell"])
    notebook = _written(registry.path_for(_SOURCE_KEY), content)

    task = notebook_step(**_base_call(registry, notebook))

    assert isinstance(task, Task)


def test_a_code_cell_with_an_int_source_after_the_parameters_cell_is_not_read(
    tmp_path: Path,
) -> None:
    """A valid parameters cell followed by a code cell whose source is an int compiles to a Task."""
    registry = _registry(tmp_path)
    cells = [
        code_cell(_BASE_PARAMETERS, tags=["parameters"]),
        {**code_cell(""), "source": 5},
    ]
    notebook = _written(registry.path_for(_SOURCE_KEY), cells)

    task = notebook_step(**_base_call(registry, notebook))

    assert isinstance(task, Task)


# Order, timing and dependencies of the check


def test_a_reserved_name_to_a_notebook_with_no_parameters_cell_keeps_the_reserved_name_message(
    tmp_path: Path,
) -> None:
    """A reserved name such as '__command__' passed to a notebook with no parameters cell raises the existing reserved-name ValueError ("is reserved"), not the no-parameters-cell one."""
    source = _cells_source(tmp_path, [code_cell("pass")])

    message = _rejection(tmp_path, source, parameters={"__command__": 1})

    assert "is reserved" in message, message
    assert "has no cell tagged" not in message, message


def test_a_missing_notebook_passed_names_keeps_the_missing_notebook_message(
    tmp_path: Path,
) -> None:
    """A step passing names over a notebook path that does not exist raises the existing "does not exist or is not a file" ValueError."""
    missing = tmp_path / "notebooks" / "missing.ipynb"

    message = _rejection(tmp_path, missing, parameters={"location": "179"})

    assert "does not exist or is not a file" in message, message


def test_the_check_runs_again_after_the_notebook_is_edited(tmp_path: Path) -> None:
    """A step that compiles, then its parameters cell edited so it no longer assigns a passed name, then the same call again raises ValueError naming that name."""
    source = _checked_source(tmp_path, "location = None")
    assert isinstance(_step(tmp_path, source, parameters={"location": "179"}), Task)

    _checked_source(tmp_path, "region = None")
    message = _rejection(tmp_path, source, parameters={"location": "179"})

    assert "'location' (parameters" in message, message


def test_the_check_imports_neither_papermill_nor_nbformat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With sys.modules['papermill'] and sys.modules['nbformat'] set to None, a step passing names over a valid notebook compiles and one passing an unassigned name raises the new ValueError."""
    # A None entry in sys.modules makes any import of that name raise ImportError.
    modules = cast(dict[str, object], sys.modules)
    monkeypatch.setitem(modules, "papermill", None)
    monkeypatch.setitem(modules, "nbformat", None)
    source = _checked_source(tmp_path, "location = None")

    task = _step(tmp_path, source, parameters={"location": "179"})
    message = _rejection(tmp_path, source, parameters={"loaction": "179"})

    assert isinstance(task, Task)
    assert "'loaction' (parameters; did you mean 'location'?)" in message, message


def test_importing_notebook_step_imports_neither_papermill_nor_nbformat() -> None:
    """A fresh sys.executable that sets sys.modules['papermill'] and sys.modules['nbformat'] to None and then imports vivarium.dagger.frontend.notebook_step exits 0."""
    code = (
        "import sys\n"
        "sys.modules['papermill'] = None\n"
        "sys.modules['nbformat'] = None\n"
        "import vivarium.dagger.frontend.notebook_step\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )

    assert completed.returncode == 0, completed.stderr
