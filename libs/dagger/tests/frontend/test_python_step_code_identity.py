"""Tests of the code identity that a python step gets, and of builds that use it."""

import importlib
import os
import sys
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from types import FunctionType, ModuleType

import pandas as pd
import pytest
from pytask import TaskOutcome

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


def test_a_target_without_a_source_file_gets_an_always_stale_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A python step whose target's module has no source file (a function whose __module__ names a module without __file__) gets an always-stale code identity named "<step>::code"."""
    module = ModuleType("ci_module_without_a_file")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    # A function's __module__ is the __name__ of the globals it is made with.
    target = FunctionType(_constant.__code__, vars(module), "make")
    assert target.__module__ == module.__name__
    pipeline = Pipeline(tmp_path / "out", "run1", repository_root=tmp_path)

    pipeline.add_python_step(target, inputs={}, outputs="a.parquet")

    [task] = freeze(pipeline).tasks
    assert isinstance(task.code_id, AlwaysStaleNode), repr(task.code_id)
    assert task.code_id.name == "a.parquet::code"


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
