"""Tests for the workflow_config Python API in ``interface.py``."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from vivarium_cluster_tools.psimulate.workflow_config.config import (
    BaseStepConfig,
    CommandStepConfig,
    NotebookStepConfig,
    PytestStepConfig,
    PythonStepConfig,
    ResourceConfig,
    SimulationStepConfig,
)
from vivarium_cluster_tools.psimulate.workflow_config.interface import (
    get_command_step_tasks,
    get_notebook_step_tasks,
    get_pytest_step_tasks,
    get_python_step_tasks,
    get_simulation_step_tasks,
)
from vivarium_cluster_tools.psimulate.workflow_config.utilities import (
    BUILD_TIMESTAMP_FILENAME,
    get_or_create_build_timestamp,
    resolve_step_env_prefix,
)


def _resources() -> ResourceConfig:
    return ResourceConfig(memory_gb=4, project="proj_simscience", queue="all.q")


_TOOL: Any = "tool-sentinel"
_BUILD_TIMESTAMP = "2026_05_18_10_00_00"
_TASKS = ["task-sentinel"]


@pytest.fixture()
def patch_get_tasks(mocker: MockerFixture) -> dict[type[BaseStepConfig], MagicMock]:
    """Patch ``get_tasks`` on every step config class so API tests don't
    invoke real Jobmon task construction.

    Returns a mapping from step class to its patched ``get_tasks`` mock so
    individual tests can inspect call args.
    """
    classes: list[type[BaseStepConfig]] = [
        CommandStepConfig,
        SimulationStepConfig,
        PytestStepConfig,
        PythonStepConfig,
        NotebookStepConfig,
    ]
    return {
        cls: mocker.patch.object(cls, "get_tasks", autospec=True, return_value=_TASKS)
        for cls in classes
    }


@pytest.fixture()
def patch_resolve_env_prefix(mocker: MockerFixture) -> MagicMock:
    """Stub the conda lookup so API tests can assert on the resolved prefix
    without invoking ``conda env list``."""
    return mocker.patch(
        "vivarium_cluster_tools.psimulate.workflow_config.utilities.resolve_env_prefix",
        side_effect=lambda env: f"/envs/{env}",
    )


@pytest.fixture()
def patch_build_timestamp(mocker: MockerFixture) -> MagicMock:
    """Stub the build-timestamp helper so API tests don't touch the
    filesystem and can assert on the timestamp passed to ``get_tasks``."""
    return mocker.patch(
        "vivarium_cluster_tools.psimulate.workflow_config.interface.get_or_create_build_timestamp",
        return_value=_BUILD_TIMESTAMP,
    )


def _assert_runtime_call(
    mock: MagicMock,
    expected_self_type: type[BaseStepConfig],
    *,
    expected_env_prefix: str,
    expected_is_resume: bool = False,
) -> Any:
    """Assert ``get_tasks`` was called once with the standard runtime args.

    Returns the step instance ``get_tasks`` was bound to so callers can
    assert on the constructed step's fields.
    """
    mock.assert_called_once()
    args, kwargs = mock.call_args
    step_instance = args[0]
    assert isinstance(step_instance, expected_self_type)
    assert args[1:] == (_TOOL,)
    assert kwargs == {
        "env_prefix": expected_env_prefix,
        "build_timestamp": _BUILD_TIMESTAMP,
        "is_resume": expected_is_resume,
    }
    return step_instance


def test_get_command_step_returns_tasks(
    patch_get_tasks: dict[type[BaseStepConfig], MagicMock],
    patch_resolve_env_prefix: MagicMock,
    patch_build_timestamp: MagicMock,
) -> None:
    """API constructs a CommandStepConfig and returns the tasks from get_tasks."""
    output_directory = Path("/tmp/results")
    tasks = get_command_step_tasks(
        name="cmd",
        resources=_resources(),
        command="echo hi",
        output_directory=output_directory,
        environment="my_env",
        tool=_TOOL,
    )
    assert tasks is _TASKS
    patch_build_timestamp.assert_called_once_with(output_directory)
    step = _assert_runtime_call(
        patch_get_tasks[CommandStepConfig],
        CommandStepConfig,
        expected_env_prefix="/envs/my_env",
    )
    assert step.name == "cmd"
    assert step.command == "echo hi"
    assert step.environment == "my_env"


def test_get_simulation_step_returns_tasks(
    patch_get_tasks: dict[type[BaseStepConfig], MagicMock],
    patch_resolve_env_prefix: MagicMock,
    patch_build_timestamp: MagicMock,
    valid_model_spec_file: Path,
    valid_branch_config_file: Path,
    valid_artifact_file: Path,
) -> None:
    """API constructs a SimulationStepConfig and returns the tasks from get_tasks."""
    output_directory = Path("/tmp/results")
    tasks = get_simulation_step_tasks(
        name="sim",
        resources=_resources(),
        output_directory=output_directory,
        model_specification=valid_model_spec_file,
        branch_configuration=valid_branch_config_file,
        artifact_path=valid_artifact_file,
        backup_freq=600.0,
        sim_verbosity=2,
        environment="sim_env",
        tool=_TOOL,
        is_resume=True,
    )
    assert tasks is _TASKS
    patch_build_timestamp.assert_called_once_with(output_directory)
    step = _assert_runtime_call(
        patch_get_tasks[SimulationStepConfig],
        SimulationStepConfig,
        expected_env_prefix="/envs/sim_env",
        expected_is_resume=True,
    )
    assert step.model_specification == valid_model_spec_file
    assert step.branch_configuration == valid_branch_config_file
    assert step.artifact_path == valid_artifact_file
    assert step.backup_freq == 600.0
    assert step.sim_verbosity == 2
    assert step.environment == "sim_env"


def test_get_pytest_step_returns_tasks(
    patch_get_tasks: dict[type[BaseStepConfig], MagicMock],
    patch_resolve_env_prefix: MagicMock,
    patch_build_timestamp: MagicMock,
    valid_pytest_path: str,
) -> None:
    """API constructs a PytestStepConfig and returns the tasks from get_tasks."""
    tasks = get_pytest_step_tasks(
        name="tests",
        resources=_resources(),
        output_directory=Path("/tmp/results"),
        path=valid_pytest_path,
        k="test_foo",
        runslow=True,
        environment="test_env",
        tool=_TOOL,
    )
    assert tasks is _TASKS
    step = _assert_runtime_call(
        patch_get_tasks[PytestStepConfig],
        PytestStepConfig,
        expected_env_prefix="/envs/test_env",
    )
    assert step.path == valid_pytest_path
    assert step.k == "test_foo"
    assert step.runslow is True


def test_get_python_step_returns_tasks(
    patch_get_tasks: dict[type[BaseStepConfig], MagicMock],
    patch_resolve_env_prefix: MagicMock,
    patch_build_timestamp: MagicMock,
    valid_python_script: str,
) -> None:
    """API constructs a PythonStepConfig and returns the tasks from get_tasks.

    path/positional_args/keyword_args are bundled into the dataclass's
    ``args`` dict.
    """
    tasks = get_python_step_tasks(
        name="script",
        resources=_resources(),
        output_directory=Path("/tmp/results"),
        path=valid_python_script,
        positional_args=["foo", 42],
        keyword_args={"verbose": True, "out_dir": "/tmp/out"},
        environment="py_env",
        tool=_TOOL,
    )
    assert tasks is _TASKS
    step = _assert_runtime_call(
        patch_get_tasks[PythonStepConfig],
        PythonStepConfig,
        expected_env_prefix="/envs/py_env",
    )
    assert step.args["path"] == valid_python_script
    assert step.args["positional_args"] == ["foo", 42]
    assert step.args["keyword_args"] == {"verbose": True, "out_dir": "/tmp/out"}


def test_get_notebook_step_returns_tasks(
    patch_get_tasks: dict[type[BaseStepConfig], MagicMock],
    patch_resolve_env_prefix: MagicMock,
    patch_build_timestamp: MagicMock,
    valid_notebook_path: Path,
) -> None:
    """API constructs a NotebookStepConfig and returns the tasks from get_tasks."""
    tasks = get_notebook_step_tasks(
        name="nb",
        resources=_resources(),
        output_directory=Path("/tmp/results"),
        path=valid_notebook_path,
        output_path=Path("/tmp/results/out.ipynb"),
        parameters={"year": 2020, "verbose": True},
        cwd=valid_notebook_path.parent,
        environment="nb_env",
        tool=_TOOL,
    )
    assert tasks is _TASKS
    step = _assert_runtime_call(
        patch_get_tasks[NotebookStepConfig],
        NotebookStepConfig,
        expected_env_prefix="/envs/nb_env",
    )
    assert step.path == valid_notebook_path
    assert step.output_path == Path("/tmp/results/out.ipynb")
    assert step.parameters == {"year": 2020, "verbose": True}
    assert step.cwd == valid_notebook_path.parent


def test_step_env_falls_back_to_conda_default_env(
    patch_get_tasks: dict[type[BaseStepConfig], MagicMock],
    patch_resolve_env_prefix: MagicMock,
    patch_build_timestamp: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``environment`` is omitted, env_prefix resolves from the runner's
    active ``CONDA_DEFAULT_ENV``."""
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "runner_env")
    get_command_step_tasks(
        name="cmd",
        resources=_resources(),
        command="echo hi",
        output_directory=Path("/tmp/results"),
        tool=_TOOL,
    )
    _assert_runtime_call(
        patch_get_tasks[CommandStepConfig],
        CommandStepConfig,
        expected_env_prefix="/envs/runner_env",
    )


@pytest.mark.parametrize(
    "api_fn, extra_kwargs, expected_error",
    [
        # Empty command -> CommandStepConfig._validate raises ValueError.
        (get_command_step_tasks, {"command": ""}, ValueError),
        # Nonexistent paths -> SimulationStepConfig._validate_required_paths
        # raises FileNotFoundError.
        (
            get_simulation_step_tasks,
            {
                "model_specification": Path("/nonexistent/model.yaml"),
                "branch_configuration": Path("/nonexistent/branches.yaml"),
            },
            FileNotFoundError,
        ),
        # Neither path nor k -> PytestStepConfig._validate raises ValueError.
        (get_pytest_step_tasks, {}, ValueError),
        # Nonexistent script -> PythonStepConfig._validate_required_paths
        # raises FileNotFoundError.
        (get_python_step_tasks, {"path": "/nonexistent/script.py"}, FileNotFoundError),
        # Bad notebook extension -> NotebookStepConfig._validate raises ValueError.
        (
            get_notebook_step_tasks,
            {
                "path": Path("/tmp/not_a_notebook.txt"),
                "output_path": Path("/tmp/out.ipynb"),
            },
            ValueError,
        ),
    ],
    ids=["command", "simulation", "pytest", "python", "notebook"],
)
def test_validation_propagates_through_api(
    api_fn: Callable[..., Any],
    extra_kwargs: dict[str, Any],
    expected_error: type[Exception],
) -> None:
    """Dataclass __post_init__ validation must fire when constructing via the API.

    Validation fires during construction, before ``get_tasks`` is called,
    so no patching of ``get_tasks`` is required.
    """
    common: dict[str, Any] = {
        "name": "bad",
        "resources": _resources(),
        "output_directory": Path("/tmp/results"),
        "tool": _TOOL,
    }
    with pytest.raises(expected_error):
        api_fn(**{**common, **extra_kwargs})


class TestResolveStepEnvPrefix:
    """Verify the precedence and validation in ``resolve_step_env_prefix``."""

    @pytest.fixture(autouse=True)
    def patch_resolve_env_prefix(self, mocker: MockerFixture) -> None:
        """Stub out the conda lookup so tests exercise only the precedence chain."""
        mocker.patch(
            "vivarium_cluster_tools.psimulate.workflow_config.utilities.resolve_env_prefix",
            side_effect=lambda env: f"/envs/{env}",
        )

    @staticmethod
    def _step(environment: str | None) -> CommandStepConfig:
        return CommandStepConfig(
            name="s",
            resources=_resources(),
            command="echo hi",
            output_directory=Path("/tmp/results"),
            environment=environment,
        )

    def test_step_environment_takes_priority(self) -> None:
        step = self._step(environment="step_env")
        assert (
            resolve_step_env_prefix(step, default_environment="workflow_env")
            == "/envs/step_env"
        )

    def test_default_environment_used_when_step_unset(self) -> None:
        step = self._step(environment=None)
        assert (
            resolve_step_env_prefix(step, default_environment="workflow_env")
            == "/envs/workflow_env"
        )

    def test_conda_default_env_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CONDA_DEFAULT_ENV", "conda_env")
        step = self._step(environment=None)
        assert resolve_step_env_prefix(step) == "/envs/conda_env"

    def test_rejects_base_environment(self) -> None:
        step = self._step(environment="base")
        with pytest.raises(ValueError, match="non-base conda environment is required"):
            resolve_step_env_prefix(step)

    def test_raises_when_nothing_resolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CONDA_DEFAULT_ENV", raising=False)
        step = self._step(environment=None)
        with pytest.raises(ValueError, match="non-base conda environment is required"):
            resolve_step_env_prefix(step)


class TestGetOrCreateBuildTimestamp:
    """Verify the persisted-vs-fresh behavior of the timestamp helper."""

    def test_creates_and_persists_timestamp_on_first_call(self, tmp_path: Path) -> None:
        ts = get_or_create_build_timestamp(tmp_path)
        assert (tmp_path / BUILD_TIMESTAMP_FILENAME).read_text().strip() == ts

    def test_reuses_persisted_timestamp_on_subsequent_calls(self, tmp_path: Path) -> None:
        (tmp_path / BUILD_TIMESTAMP_FILENAME).write_text("2020_01_01_00_00_00")
        assert get_or_create_build_timestamp(tmp_path) == "2020_01_01_00_00_00"

    def test_creates_missing_output_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "does" / "not" / "exist"
        ts = get_or_create_build_timestamp(target)
        assert (target / BUILD_TIMESTAMP_FILENAME).read_text().strip() == ts
