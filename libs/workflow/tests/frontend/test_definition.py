"""Tests for loading a pipeline definition (``frontend.definition``)."""

import importlib
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytask
import pytest

from vivarium.workflow.frontend.definition import (
    DEFAULT_FUNCTION,
    DefinitionError,
    load_pipeline,
)
from vivarium.workflow.frontend.pipeline import Pipeline
from vivarium.workflow.frontend.python_step import (
    PythonStepRun,
    resolve_reference,
    target_reference,
)

_TWO_FUNCTIONS = """\
from pathlib import Path

from vivarium.workflow.backend.task import Task
from vivarium.workflow.frontend import Pipeline

HERE = Path(__file__).resolve().parent


def one_step(name):
    task = Task(name=name, run="true", inputs={}, outputs={}, resources={}, env=None)
    return Pipeline([task], HERE / "state")


def pipeline():
    return one_step("from_pipeline")


def other():
    return one_step("from_other")
"""
"""A definition whose pipeline() and other() each return one step named for the function."""

_PRODUCING = """\
from pathlib import Path

import pandas as pd

from vivarium.workflow.backend.wiring import ArtifactRegistry
from vivarium.workflow.frontend import Pipeline, python_step

HERE = Path(__file__).resolve().parent


def make_table():
    return pd.DataFrame({"n": [1, 2, 3]})


def pipeline():
    registry = ArtifactRegistry(HERE / "out", "run1")
    task = python_step(make_table, inputs={}, produces="table.parquet", registry=registry)
    return Pipeline([task], HERE / "state", registry)
"""
"""A definition whose one python_step would write 'table.parquet' if it ran."""

_RETURNS_TASKS = """\
from vivarium.workflow.backend.task import Task


def pipeline():
    return [Task(name="a", run="true", inputs={}, outputs={}, resources={}, env=None)]
"""
"""A definition whose pipeline() returns a list of tasks instead of a Pipeline."""

_RAISES = {
    "module": 'raise LookupError("raised by the definition")\n',
    "function": 'def pipeline():\n    raise LookupError("raised by the definition")\n',
}
"""Definitions that raise at their top level or inside pipeline()."""


def _token() -> str:
    """Return a random suffix that keeps a module name unique to one test."""
    return uuid.uuid4().hex[:12]


def _write(directory: Path, source: str, name: str | None = None) -> Path:
    """Write ``source`` to ``directory/name``, or to a new uniquely named .py file."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name or f"def_{_token()}.py")
    path.write_text(source)
    return path


def _package(tmp_path: Path, source: str) -> str:
    """Write ``source`` as module 'mod' of a new package on ``sys.path``; return its name."""
    package = f"defs_{_token()}"
    _write(tmp_path / package, "", "__init__.py")
    _write(tmp_path / package, source, "mod.py")
    sys.path.insert(0, str(tmp_path))
    importlib.invalidate_caches()
    return f"{package}.mod"


def _one_step(name: str) -> str:
    """Return a definition whose pipeline() has one step named ``name``."""
    return _TWO_FUNCTIONS.replace('"from_pipeline"', f'"{name}"')


def _step_names(pipeline: Pipeline) -> list[str]:
    """Return the names of ``pipeline``'s steps."""
    return [task.name for task in pipeline.tasks]


def _loaded_from(module: object, directory: Path) -> bool:
    """Return whether ``module``'s file is under ``directory`` or is a relative path."""
    file = getattr(module, "__file__", None)
    if not isinstance(file, str):
        return False
    path = Path(file)
    return not path.is_absolute() or path.resolve().is_relative_to(directory.resolve())


@pytest.fixture(autouse=True)
def _forget_loaded_definitions(tmp_path: Path) -> Iterator[None]:
    """Restore ``sys.path`` and drop the modules a test loaded from its definition files."""
    path = list(sys.path)
    modules = set(sys.modules)
    yield
    sys.path[:] = path
    # Only definition modules are dropped: a compiled extension that a test imported
    # lazily, such as pyarrow's dataset module, cannot be imported a second time. A
    # definition loaded by a relative spec can keep a relative __file__.
    for name in set(sys.modules) - modules:
        if _loaded_from(sys.modules.get(name), tmp_path):
            del sys.modules[name]


# Accepted specs


@pytest.mark.parametrize("form", ["file", "file:function", "module", "module:function"])
def test_load_pipeline_accepts_a_file_or_a_dotted_module_with_an_optional_function(
    tmp_path: Path, form: str
) -> None:
    """load_pipeline returns the Pipeline built by the named function for each of path/to/file.py, file.py:other, pkg.mod and pkg.mod:other."""
    if form.startswith("file"):
        base = str(_write(tmp_path / "path" / "to", _TWO_FUNCTIONS))
    else:
        base = _package(tmp_path, _TWO_FUNCTIONS)
    named = form.endswith(":function")

    pipeline = load_pipeline(f"{base}:other" if named else base)

    assert isinstance(pipeline, Pipeline)
    assert _step_names(pipeline) == ["from_other" if named else "from_pipeline"]


def test_the_default_function_is_pipeline(tmp_path: Path) -> None:
    """DEFAULT_FUNCTION is 'pipeline', and a spec with no ':function' calls the definition's pipeline()."""
    path = _write(tmp_path, _TWO_FUNCTIONS)

    pipeline = load_pipeline(str(path))

    assert DEFAULT_FUNCTION == "pipeline"
    assert _step_names(pipeline) == ["from_pipeline"]


def test_a_relative_file_path_is_relative_to_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spec 'sub/def.py' given from a working directory that holds sub/def.py loads that file."""
    path = _write(tmp_path / "sub", _one_step("from_sub"))
    monkeypatch.chdir(tmp_path)

    pipeline = load_pipeline(f"sub/{path.name}")

    assert _step_names(pipeline) == ["from_sub"]


def test_text_after_the_last_colon_that_is_not_an_identifier_is_part_of_the_path(
    tmp_path: Path,
) -> None:
    """A file whose own name contains ':' followed by a non-identifier (for example 'run:1.py') loads as a whole path with the default function."""
    path = _write(tmp_path, _TWO_FUNCTIONS, f"run_{_token()}:1.py")

    pipeline = load_pipeline(str(path))

    assert _step_names(pipeline) == ["from_pipeline"]


# Loading builds nothing


def test_load_pipeline_builds_nothing_and_writes_no_state(tmp_path: Path) -> None:
    """After loading a definition whose steps would write products, the root has no pytask.lock, no .pytask directory and no pytask.toml, and no product file exists."""
    path = _write(tmp_path, _PRODUCING)

    pipeline = load_pipeline(str(path))

    for name in ("pytask.lock", ".pytask", "pytask.toml"):
        assert not (pipeline.root / name).exists(), name
    assert pipeline.registry is not None
    assert not pipeline.registry.path_for("table.parquet").exists()


# A file definition is importable by reference


def test_a_file_is_imported_as_a_module_named_by_its_stem(tmp_path: Path) -> None:
    """After loading def_x.py by path, sys.modules['def_x'].__file__ is that file and a step function defined in it has __module__ == 'def_x'."""
    path = _write(tmp_path, _PRODUCING)

    [task] = load_pipeline(str(path)).tasks

    module = sys.modules[path.stem]
    assert module.__file__ is not None
    assert Path(module.__file__).resolve() == path.resolve()
    run = task.run
    assert isinstance(run, PythonStepRun)
    assert run.target.__module__ == path.stem


def test_a_step_function_in_a_file_definition_is_importable_by_reference(
    tmp_path: Path,
) -> None:
    """target_reference of a function defined in a path-loaded file succeeds and resolve_reference returns that same function."""
    path = _write(tmp_path, _PRODUCING)
    [task] = load_pipeline(str(path)).tasks
    run = task.run
    assert isinstance(run, PythonStepRun)

    reference = target_reference(run.target)

    assert reference == f"{path.stem}:make_table"
    assert resolve_reference(reference) is run.target


def test_the_file_directory_is_put_at_the_front_of_sys_path_once(tmp_path: Path) -> None:
    """Loading a file puts its directory at sys.path[0]; loading it again does not add a second entry."""
    path = _write(tmp_path / "defs", _TWO_FUNCTIONS)
    directory = path.parent.resolve()

    load_pipeline(str(path))
    assert Path(sys.path[0]).resolve() == directory
    load_pipeline(str(path))

    assert sum(1 for entry in sys.path if Path(entry).resolve() == directory) == 1


def test_loading_an_edited_file_again_picks_up_the_edit(tmp_path: Path) -> None:
    """Loading a file, editing it so its pipeline has a different step, and loading it again in the same process returns the new step."""
    path = _write(tmp_path, _one_step("before_edit"))
    assert _step_names(load_pipeline(str(path))) == ["before_edit"]

    # The edit changes the file's length, so the bytecode cache cannot serve the old code
    # even when the edit lands within the same second.
    path.write_text(_one_step("after_the_edit"))

    assert _step_names(load_pipeline(str(path))) == ["after_the_edit"]


def test_a_file_whose_stem_names_another_imported_module_is_refused(tmp_path: Path) -> None:
    """Loading a file named pytask.py raises DefinitionError saying to rename the file, and leaves sys.modules['pytask'] unchanged."""
    path = _write(tmp_path, _TWO_FUNCTIONS, "pytask.py")

    with pytest.MonkeyPatch.context() as patch:
        # Put pytask back even if the loader replaces it, so that later tests still run.
        patch.setitem(sys.modules, "pytask", pytask)
        with pytest.raises(DefinitionError, match="(?i)rename"):
            load_pipeline(str(path))
        assert sys.modules["pytask"] is pytask


# Definition errors


def test_a_missing_file_raises_definition_error(tmp_path: Path) -> None:
    """A spec naming a .py file that does not exist raises DefinitionError naming the spec."""
    spec = str(tmp_path / f"missing_{_token()}.py")

    with pytest.raises(DefinitionError) as info:
        load_pipeline(spec)

    assert spec in str(info.value)


def test_a_missing_module_raises_definition_error() -> None:
    """A dotted spec whose module (or a parent package) does not exist raises DefinitionError naming the spec."""
    specs = ["no_such_package_vwf.definition", "vivarium.workflow.no_such_module_vwf"]
    for spec in specs:
        with pytest.raises(DefinitionError) as info:
            load_pipeline(spec)

        assert spec in str(info.value)


def test_an_import_error_inside_a_dotted_module_propagates_unchanged(tmp_path: Path) -> None:
    """A dotted module that exists but imports a missing module raises that ModuleNotFoundError, not DefinitionError."""
    dependency = f"no_such_dependency_{_token()}"
    spec = _package(tmp_path, f"import {dependency}\n")

    with pytest.raises(ModuleNotFoundError) as info:
        load_pipeline(spec)

    assert info.value.name == dependency


def test_a_missing_function_raises_definition_error(tmp_path: Path) -> None:
    """A definition without the named function raises DefinitionError naming the function, with a one-line message."""
    path = _write(tmp_path, _TWO_FUNCTIONS)

    with pytest.raises(DefinitionError) as info:
        load_pipeline(f"{path}:no_such_function")

    message = str(info.value)
    assert "no_such_function" in message
    assert len(message.splitlines()) == 1, message


def test_a_non_callable_attribute_raises_definition_error(tmp_path: Path) -> None:
    """A definition whose 'pipeline' attribute is not callable (for example a list) raises DefinitionError with a one-line message."""
    path = _write(tmp_path, 'pipeline = ["not", "a", "function"]\n')

    with pytest.raises(DefinitionError) as info:
        load_pipeline(str(path))

    assert len(str(info.value).splitlines()) == 1, str(info.value)


def test_a_function_returning_something_other_than_a_pipeline_raises_definition_error(
    tmp_path: Path,
) -> None:
    """A function that returns a list of tasks instead of a Pipeline raises DefinitionError naming the returned type, with a one-line message."""
    path = _write(tmp_path, _RETURNS_TASKS)

    with pytest.raises(DefinitionError) as info:
        load_pipeline(str(path))

    message = str(info.value)
    assert "list" in message
    assert len(message.splitlines()) == 1, message


@pytest.mark.parametrize("where", ["module", "function"])
def test_an_exception_raised_by_the_definition_propagates_unchanged(
    tmp_path: Path, where: str
) -> None:
    """An exception raised at the definition's top level or inside its function propagates as the same type and message, not as DefinitionError."""
    path = _write(tmp_path, _RAISES[where])

    with pytest.raises(LookupError, match="raised by the definition") as info:
        load_pipeline(str(path))

    assert type(info.value) is LookupError


def test_a_file_that_fails_to_import_leaves_no_module_behind(tmp_path: Path) -> None:
    """When importing a definition file raises, sys.modules has no entry for its stem afterwards."""
    path = _write(tmp_path, _RAISES["module"])

    with pytest.raises(LookupError):
        load_pipeline(str(path))

    assert path.stem not in sys.modules
