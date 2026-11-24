"""Tests of the code identity that a python step gets, and of builds that use it."""

import importlib
import os
import re
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from types import FunctionType, ModuleType
from typing import cast

import pandas as pd
import pytest
from pytask import PNode, TaskOutcome

from tests.bridge.helpers import LONG_RECORD_INTERVAL, bound_names
from tests.frontend.notebooks import write_source_notebook
from tests.frontend.simulations import write_branches, write_spec
from vivarium.dagger.backend.code_identity import CodeIdentityNode
from vivarium.dagger.backend.nodes import AlwaysStaleNode
from vivarium.dagger.bridge import execute
from vivarium.dagger.frontend import preview, run
from vivarium.dagger.frontend.builder import Pipeline, freeze
from vivarium.dagger.harness import FakeJobmon

ImportFrom = Callable[[Path, str], ModuleType]
"""Import the named module from a directory that is put first on sys.path."""

_CHAIN = ("a.parquet", "b.parquet")
"""The steps of the chain that :func:`_chain` adds, in order."""

_PROJECT = {"project": "proj_test"}
"""The ``jobmon_resources`` that give every step a project."""

_STEPS = """\
import pandas as pd


def make() -> pd.DataFrame:
    return pd.DataFrame({"n": [1, 2, 3]})


def double(source: str) -> pd.DataFrame:
    return pd.DataFrame({"n": pd.read_parquet(source)["n"] * 2})


def split() -> dict[str, pd.DataFrame]:
    return {"x": pd.DataFrame({"n": [1]}), "y": pd.DataFrame({"n": [2]})}
"""
"""A module of step functions that imports only installed code."""


def _write(root: Path, files: Mapping[str, str]) -> Path:
    """Write each file under ``root``, making directories; return ``root``."""
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def _constant() -> pd.DataFrame:
    """Return a fixed one-column table."""
    return pd.DataFrame({"n": [1, 2, 3]})


def _chain(module: ModuleType, tmp_path: Path, repository_root: Path | None) -> Pipeline:
    """Return a pipeline of ``module.make`` and then ``module.double`` under ``tmp_path``."""
    pipeline = Pipeline(tmp_path / "out", "run1", repository_root=repository_root)
    pipeline.add_python_step(module.make, inputs={}, outputs="a.parquet")
    pipeline.add_python_step(
        module.double, inputs={"source": "a.parquet"}, outputs="b.parquet"
    )
    return pipeline


@pytest.fixture
def import_from(monkeypatch: pytest.MonkeyPatch) -> Iterator[ImportFrom]:
    """Return a function that imports a module from a directory put first on sys.path.

    Every module imported from such a directory is removed from sys.modules after the
    test, so tests do not share modules, and no bytecode is written next to the files.
    """
    directories: list[Path] = []
    monkeypatch.setattr(sys, "dont_write_bytecode", True)

    def import_module(directory: Path, name: str) -> ModuleType:
        monkeypatch.syspath_prepend(str(directory))
        directories.append(directory.resolve())
        return importlib.import_module(name)

    yield import_module
    for name, module in list(sys.modules.items()):
        file = getattr(module, "__file__", None)
        if not isinstance(file, str):
            continue
        if any(Path(file).resolve().is_relative_to(directory) for directory in directories):
            del sys.modules[name]


# Wiring


def test_a_pipeline_resolves_and_exposes_its_repository_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pipeline(repository_root=<relative path>) exposes the resolved path as repository_root, and a pipeline made without it exposes None."""
    (tmp_path / "repo").mkdir()
    monkeypatch.chdir(tmp_path)

    declared = Pipeline(tmp_path / "out", "run1", repository_root="repo")
    undeclared = Pipeline(tmp_path / "out", "run1")

    assert declared.repository_root == (tmp_path / "repo").resolve()
    assert undeclared.repository_root is None


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_a_repository_root_that_is_not_a_directory_is_refused(
    tmp_path: Path, kind: str
) -> None:
    """Pipeline(repository_root=...) raises ValueError naming the resolved path when it does not exist or is a file."""
    root = tmp_path / "repo"
    if kind == "file":
        root.write_text("not a directory\n")

    with pytest.raises(ValueError, match=re.escape(str(root.resolve()))):
        Pipeline(tmp_path / "out", "run1", repository_root=root)


def test_a_python_step_gets_a_code_identity_node_that_starts_at_its_targets_module(
    tmp_path: Path, import_from: ImportFrom
) -> None:
    """A step added with add_python_step, with one output key and with a mapping of outputs, has a CodeIdentityNode as its task's code_id, named "<step>::code", whose start is the resolved file of the target's module and whose repository root is the pipeline's."""
    repo = _write(tmp_path / "repo", {"ci_wired_steps.py": _STEPS})
    module = import_from(repo, "ci_wired_steps")
    assert module.__file__ is not None
    pipeline = Pipeline(tmp_path / "out", "run1", repository_root=repo)

    pipeline.add_python_step(module.make, inputs={}, outputs="a.parquet")
    pipeline.add_python_step(
        module.split, inputs={}, outputs={"x": "x.parquet", "y": "y.csv"}, name="split"
    )

    tasks = {task.name: task for task in freeze(pipeline).tasks}
    for name in ("a.parquet", "split"):
        node = tasks[name].code_id
        assert isinstance(node, CodeIdentityNode), repr(node)
        assert node.name == f"{name}::code"
        assert node.start == Path(module.__file__).resolve()
        assert node.repository_root == repo.resolve()


def test_a_python_steps_code_identity_carries_its_targets_module(
    tmp_path: Path, import_from: ImportFrom
) -> None:
    """A python step whose target is defined in the submodule ci_modpkg.steps of a package gets a CodeIdentityNode whose module is the target's __module__."""
    repo = _write(
        tmp_path / "repo", {"ci_modpkg/__init__.py": "", "ci_modpkg/steps.py": _STEPS}
    )
    module = import_from(repo, "ci_modpkg.steps")

    node = _code_node(module.make, tmp_path, repo)

    assert isinstance(node, CodeIdentityNode), repr(node)
    assert module.make.__module__ == "ci_modpkg.steps"
    assert node.module == "ci_modpkg.steps"


_LOOPED_WRAPPERS = f"""\
{_STEPS}

def make_v2() -> pd.DataFrame:
    return pd.DataFrame({{"n": [4, 5, 6]}})


make.__wrapped__ = make_v2
make_v2.__wrapped__ = make
"""
"""A module of step functions in which make and make_v2 each name the other as the
function they wrap."""


def _function_in_module(module: ModuleType) -> FunctionType:
    """Return a function named make whose __module__ is ``module``'s name."""
    # A function's __module__ is the __name__ of the globals it is made with.
    target = FunctionType(_constant.__code__, vars(module), "make")
    assert target.__module__ == module.__name__
    return target


def _target_without_a_source(
    kind: str, repo: Path, monkeypatch: pytest.MonkeyPatch, import_from: ImportFrom
) -> Callable[..., pd.DataFrame]:
    """Return a step target of ``kind`` whose source file cannot be found."""
    if kind == "module_without_a_file":
        module = ModuleType("ci_module_without_a_file")
        monkeypatch.setitem(sys.modules, module.__name__, module)
        return _function_in_module(module)
    if kind == "module_file_not_a_source":
        module = ModuleType("ci_module_of_an_extension")
        extension = repo / "ci_module_of_an_extension.so"
        extension.write_bytes(b"\x7fELF\x02\x01\x01 a compiled module")
        module.__file__ = str(extension)
        target = _function_in_module(module)
        setattr(module, "make", target)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        return target
    if kind == "module_not_imported":
        name = "ci_module_not_in_sys_modules"
        monkeypatch.delitem(sys.modules, name, raising=False)
        return FunctionType(_constant.__code__, {"__name__": name}, "make")
    if kind == "wrapped_loop":
        _write(repo, {"ci_looped_steps.py": _LOOPED_WRAPPERS})
        looped = import_from(repo, "ci_looped_steps").make
        assert looped.__wrapped__.__wrapped__ is looped
        return cast(Callable[..., pd.DataFrame], looped)
    raise AssertionError(f"no target kind named {kind}")


@pytest.mark.parametrize(
    "kind",
    [
        "module_without_a_file",
        "module_file_not_a_source",
        "module_not_imported",
        "wrapped_loop",
    ],
)
def test_a_target_whose_source_file_cannot_be_found_gets_an_always_stale_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, import_from: ImportFrom, kind: str
) -> None:
    """A python step gets an always-stale code identity named "<step>::code" when its target's __module__ names a module without __file__, a module in sys.modules whose __file__ ends in ".so" and so is not a source file, or a module that is not in sys.modules, or when the target's __wrapped__ chain loops (make.__wrapped__ is make_v2 and make_v2.__wrapped__ is make)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = _target_without_a_source(kind, repo, monkeypatch, import_from)
    pipeline = Pipeline(tmp_path / "out", "run1", repository_root=repo)

    pipeline.add_python_step(target, inputs={}, outputs="a.parquet")

    [task] = freeze(pipeline).tasks
    assert isinstance(task.code_id, AlwaysStaleNode), repr(task.code_id)
    assert task.code_id.name == "a.parquet::code"


def _code_node(target: Callable[..., pd.DataFrame], tmp_path: Path, repo: Path) -> PNode:
    """Return the code identity of a one-step pipeline that calls ``target``."""
    pipeline = Pipeline(tmp_path / "out", "run1", repository_root=repo)
    pipeline.add_python_step(target, inputs={}, outputs="a.parquet")
    [task] = freeze(pipeline).tasks
    assert task.code_id is not None
    return task.code_id


_MAKE_V2 = """

def make_v2() -> pd.DataFrame:
    return pd.DataFrame({"n": [4, 5, 6]})
"""
"""A second version of ``make`` in the same module."""


def test_switching_a_step_to_another_function_in_the_same_module_changes_its_state(
    tmp_path: Path, import_from: ImportFrom
) -> None:
    """A step of one name that calls make and then make_v2 of one unchanged module gets unequal code identity nodes with different states, while two builds that both call make get equal nodes with equal states."""
    repo = _write(tmp_path / "repo", {"ci_switch_steps.py": _STEPS + _MAKE_V2})
    module = import_from(repo, "ci_switch_steps")

    make, make_again, make_v2 = [
        _code_node(target, tmp_path, repo)
        for target in (module.make, module.make, module.make_v2)
    ]

    for node in (make, make_again, make_v2):
        assert isinstance(node, CodeIdentityNode), repr(node)
    assert make == make_again
    assert make.state() == make_again.state()
    assert make_v2 != make
    assert make_v2.state() != make.state()


_DECORATOR_MODULE = """\
import functools


def logged(function):
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        return function(*args, **kwargs)

    return wrapper


def retry(times):
    def decorate(function):
        @functools.wraps(function)
        def wrapper(*args, **kwargs):
            return function(*args, **kwargs)

        return wrapper

    return decorate
"""
"""A module of two decorators that use functools.wraps, one of which takes arguments."""

_TARGET_KINDS_MODULE = """\
import functools

import pandas as pd

import ci_kinds_decorator


def make() -> pd.DataFrame:
    return pd.DataFrame({"n": [1]})


def _factory(n):
    def made():
        return pd.DataFrame({"n": [n]})

    return made


def _decorator_without_wraps(function):
    def wrapper():
        return function()

    return wrapper


class Maker:
    def __call__(self):
        return make()

    def method(self):
        return make()


factory_closure = _factory(2)
callable_instance = Maker()
bound_method = callable_instance.method
lambda_function = lambda: make()
partial_function = functools.partial(make)


@_decorator_without_wraps
def decorated_without_wraps():
    return make()


@ci_kinds_decorator.logged
def decorated_with_wraps():
    return make()


@ci_kinds_decorator.retry(times=3)
def decorated_with_arguments():
    return make()
"""
"""A module of step targets of each kind that is not a plain module-level function, and
two decorated at their definition with decorators from another module that use
functools.wraps, one of which takes arguments."""

_PIPELINE_MODULE = """\
import ci_kinds_decorator
import ci_kinds_steps

wrapped = ci_kinds_decorator.logged(ci_kinds_steps.make)
wrapped_with_arguments = ci_kinds_decorator.retry(times=3)(ci_kinds_steps.make)
"""
"""A pipeline file that wraps a function of the steps module with each decorator."""

_TARGET_KINDS_FILES = {
    "ci_kinds_decorator.py": _DECORATOR_MODULE,
    "ci_kinds_steps.py": _TARGET_KINDS_MODULE,
    "ci_kinds_pipeline.py": _PIPELINE_MODULE,
}
"""The files of the repository of the step targets of each kind."""


@pytest.mark.parametrize(
    "attribute",
    [
        "factory_closure",
        "callable_instance",
        "bound_method",
        "lambda_function",
        "partial_function",
        "decorated_without_wraps",
    ],
)
def test_a_target_that_is_not_a_plain_module_level_function_gets_an_always_stale_identity(
    tmp_path: Path, import_from: ImportFrom, attribute: str
) -> None:
    """A factory closure, a callable instance, a bound method, a lambda, a functools.partial and a function wrapped by a decorator without functools.wraps, each defined in a first-party module, get an always-stale code identity named "<step>::code"."""
    repo = _write(tmp_path / "repo", _TARGET_KINDS_FILES)
    module = import_from(repo, "ci_kinds_steps")

    node = _code_node(getattr(module, attribute), tmp_path, repo)

    assert isinstance(node, AlwaysStaleNode), repr(node)
    assert node.name == "a.parquet::code"


@pytest.mark.parametrize("attribute", ["decorated_with_wraps", "decorated_with_arguments"])
def test_a_target_decorated_with_functools_wraps_starts_at_the_wrapped_functions_module(
    tmp_path: Path, import_from: ImportFrom, attribute: str
) -> None:
    """A step whose target is decorated at its definition, in the module that exposes it under its own name, by a decorator from another module that uses functools.wraps, with or without arguments, gets a CodeIdentityNode that starts at that module and gives the same state on two calls."""
    repo = _write(tmp_path / "repo", _TARGET_KINDS_FILES)
    module = import_from(repo, "ci_kinds_steps")
    assert module.__file__ is not None

    node = _code_node(getattr(module, attribute), tmp_path, repo)

    assert isinstance(node, CodeIdentityNode), repr(node)
    assert node.start == Path(module.__file__).resolve()
    assert node.repository_root == repo.resolve()
    assert node.state() == node.state()


@pytest.mark.parametrize("attribute", ["wrapped", "wrapped_with_arguments"])
def test_a_function_wrapped_outside_its_module_gets_an_always_stale_identity(
    tmp_path: Path, import_from: ImportFrom, attribute: str
) -> None:
    """A step whose target is a function of the steps module wrapped in the pipeline file by a decorator that uses functools.wraps, with or without arguments, so that the steps module does not expose the wrapper under the wrapped function's name, gets an always-stale code identity named "<step>::code"."""
    repo = _write(tmp_path / "repo", _TARGET_KINDS_FILES)
    pipeline_module = import_from(repo, "ci_kinds_pipeline")
    target = getattr(pipeline_module, attribute)
    assert target.__wrapped__ is sys.modules["ci_kinds_steps"].make

    node = _code_node(target, tmp_path, repo)

    assert isinstance(node, AlwaysStaleNode), repr(node)
    assert node.name == "a.parquet::code"


@pytest.mark.parametrize("kind", ["command", "notebook", "simulation"])
def test_other_kinds_of_step_keep_an_always_stale_identity(tmp_path: Path, kind: str) -> None:
    """A command step, a notebook step and a simulation step still get an always-stale code identity."""
    pipeline = Pipeline(tmp_path / "out", "run1", repository_root=tmp_path)
    if kind == "command":
        pipeline.add_command_step("touch {out}", outputs={"out": "x.txt"}, name="step")
    elif kind == "notebook":
        notebook = write_source_notebook(tmp_path / "report.ipynb")
        pipeline.add_notebook_step(
            notebook, executed="run.ipynb", env="notebook_env", name="step"
        )
    else:
        model = tmp_path / "model"
        pipeline.add_simulation_step(
            spec=write_spec(model / "spec.yaml"),
            branches=write_branches(model / "branches.yaml"),
            outputs="sims/toy",
            env="simulation_env",
            options={"project": "proj_test", "no_slack": True},
            name="step",
        )

    [task] = freeze(pipeline).tasks
    assert isinstance(task.code_id, AlwaysStaleNode), repr(task.code_id)
    assert task.code_id.name == "step::code"


# Builds


def test_a_second_build_with_no_edit_skips_every_python_step(
    tmp_path: Path, import_from: ImportFrom
) -> None:
    """In process, a pipeline of two chained python steps whose functions live in a module under the declared repository root runs both steps on its first build and skips both on a second build with no edit, and the preview before the second build lists no step as would run."""
    repo = _write(tmp_path / "repo", {"ci_chain_steps.py": _STEPS})
    pipeline = _chain(import_from(repo, "ci_chain_steps"), tmp_path, repo)

    first = run(pipeline)
    plan = preview(pipeline)
    second = run(pipeline)

    assert first == dict.fromkeys(_CHAIN, TaskOutcome.SUCCESS)
    assert [step.name for step in plan if step.would_run] == []
    assert second == dict.fromkeys(_CHAIN, TaskOutcome.SKIP_UNCHANGED)


def test_a_second_build_through_the_bridge_with_no_edit_submits_nothing(
    tmp_path: Path, import_from: ImportFrom, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the bridge with the Jobmon test double, the same pipeline binds both steps on its first build and binds no workflow on a second build with no edit."""
    repo = _write(tmp_path / "repo", {"ci_bridged_steps.py": _STEPS})
    pipeline = _chain(import_from(repo, "ci_bridged_steps"), tmp_path, repo)
    # The node runners are child processes, which import the steps by reference.
    paths = [str(repo), os.environ.get("PYTHONPATH", "")]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(path for path in paths if path))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    # As the bridge tests' own fixtures do: no Slurm lookup, and no recording mid-run.
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    monkeypatch.setattr(execute, "_RECORD_INTERVAL_SECONDS", LONG_RECORD_INTERVAL)

    with FakeJobmon() as jobmon:
        first = run(pipeline, jobmon=True, jobmon_resources=dict(_PROJECT))
        second = run(pipeline, jobmon=True, jobmon_resources=dict(_PROJECT))

    assert first == dict.fromkeys(_CHAIN, TaskOutcome.SUCCESS)
    assert second == dict.fromkeys(_CHAIN, TaskOutcome.SKIP_UNCHANGED)
    assert bound_names(jobmon) == [list(_CHAIN)]


_HELPER_FILES = {
    "ci_helper.py": "def factor() -> int:\n    return 2\n",
    "ci_middle.py": "import ci_helper\n",
    "ci_direct_steps.py": (
        "import pandas as pd\n"
        "\n"
        "import ci_helper\n"
        "\n\n"
        "def one() -> pd.DataFrame:\n"
        '    return pd.DataFrame({"n": [ci_helper.factor()]})\n'
    ),
    "ci_indirect_steps.py": (
        "import pandas as pd\n"
        "\n"
        "import ci_middle\n"
        "\n\n"
        "def two() -> pd.DataFrame:\n"
        '    return pd.DataFrame({"n": [ci_middle.ci_helper.factor()]})\n'
    ),
    "ci_plain_steps.py": (
        "import pandas as pd\n"
        "\n\n"
        "def three() -> pd.DataFrame:\n"
        '    return pd.DataFrame({"n": [3]})\n'
    ),
}
"""A helper module, a module that imports it, and the modules of three steps: one that
imports the helper directly, one that imports it through the second module, and one that
does not import it."""


def test_an_edit_to_a_helper_reruns_only_the_steps_that_import_it(
    tmp_path: Path, import_from: ImportFrom
) -> None:
    """With three python steps whose functions are in three modules, of which one imports a helper module directly, one imports it through a second module, and one does not import it, a code edit to the helper re-runs exactly the first two steps and skips the third."""
    repo = _write(tmp_path / "repo", _HELPER_FILES)
    direct = import_from(repo, "ci_direct_steps")
    indirect = import_from(repo, "ci_indirect_steps")
    plain = import_from(repo, "ci_plain_steps")
    pipeline = Pipeline(tmp_path / "out", "run1", repository_root=repo)
    pipeline.add_python_step(direct.one, inputs={}, outputs="one.parquet")
    pipeline.add_python_step(indirect.two, inputs={}, outputs="two.parquet")
    pipeline.add_python_step(plain.three, inputs={}, outputs="three.parquet")
    steps = ("one.parquet", "two.parquet", "three.parquet")
    assert run(pipeline) == dict.fromkeys(steps, TaskOutcome.SUCCESS)

    (repo / "ci_helper.py").write_text("def factor() -> int:\n    return 3\n")
    rebuilt = run(pipeline)

    assert rebuilt == {
        "one.parquet": TaskOutcome.SUCCESS,
        "two.parquet": TaskOutcome.SUCCESS,
        "three.parquet": TaskOutcome.SKIP_UNCHANGED,
    }


def test_a_comment_edit_reruns_nothing(tmp_path: Path, import_from: ImportFrom) -> None:
    """A comment added to a module that every step imports re-runs no step on the next build."""
    common = "def rows() -> list[int]:\n    return [1, 2, 3]\n"
    steps = f"import ci_common\n{_STEPS}"
    repo = _write(tmp_path / "repo", {"ci_common.py": common, "ci_common_steps.py": steps})
    pipeline = _chain(import_from(repo, "ci_common_steps"), tmp_path, repo)
    assert run(pipeline) == dict.fromkeys(_CHAIN, TaskOutcome.SUCCESS)

    (repo / "ci_common.py").write_text(f"# Rows of the table.\n{common}")
    rebuilt = run(pipeline)

    assert rebuilt == dict.fromkeys(_CHAIN, TaskOutcome.SKIP_UNCHANGED)


def test_without_a_repository_root_a_step_in_the_repository_reruns_every_build(
    tmp_path: Path, import_from: ImportFrom
) -> None:
    """A pipeline made without repository_root, whose step functions live in an ordinary directory that is not an editable install, runs the steps again on a second build with no edit."""
    plain = _write(tmp_path / "plain", {"ci_undeclared_steps.py": _STEPS})
    pipeline = _chain(import_from(plain, "ci_undeclared_steps"), tmp_path, None)
    assert pipeline.repository_root is None

    first = run(pipeline)
    second = run(pipeline)

    assert first == dict.fromkeys(_CHAIN, TaskOutcome.SUCCESS)
    assert second == dict.fromkeys(_CHAIN, TaskOutcome.SUCCESS)
