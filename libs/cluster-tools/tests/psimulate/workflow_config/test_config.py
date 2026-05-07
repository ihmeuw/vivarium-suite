"""Unit tests for the workflow configuration parser."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests.psimulate.workflow_config.utilities import (
    make_pytest_step_dict,
    make_step_dict,
    make_workflow_dict,
    write_workflow_yaml,
)
from vivarium_cluster_tools.psimulate.workflow_config.config import (
    CommandStepConfig,
    PytestStepConfig,
    ResourceConfig,
    SimulationStepConfig,
    WorkflowConfig,
)


class TestWorkflowConfigFromYaml:
    """Verify that ``WorkflowConfig.from_yaml_with_cli_overrides`` correctly parses valid YAML."""

    def test_parses_workflow_fields(self, valid_workflow_yaml: Path) -> None:
        config = WorkflowConfig.from_yaml_with_cli_overrides(valid_workflow_yaml)
        assert config.name == "test_pipeline"
        assert config.project == "proj_simscience"
        assert config.queue == "all.q"
        assert config.output_directory == Path("/tmp/results")
        assert config.default_environment is None

    def test_parses_default_environment(self, tmp_path: Path) -> None:
        data = make_workflow_dict(default_environment="my_env")
        yaml_path = write_workflow_yaml(tmp_path, data)
        config = WorkflowConfig.from_yaml_with_cli_overrides(yaml_path)
        assert config.default_environment == "my_env"

    def test_step_ordering_preserved(self, valid_workflow_yaml: Path) -> None:
        config = WorkflowConfig.from_yaml_with_cli_overrides(valid_workflow_yaml)
        assert len(config.steps) == 2
        assert config.steps[0].name == "pre_tests"
        assert config.steps[1].name == "post_analysis"

    @pytest.mark.parametrize(
        "index, expected_command",
        [
            (0, "pytest tests/test_lbwsg.py tests/test_mortality.py --runslow"),
            (1, "python scripts/analyze.py --input /results"),
        ],
        ids=["structured_command", "raw_command"],
    )
    def test_parses_command_steps(
        self, valid_workflow_yaml: Path, index: int, expected_command: str
    ) -> None:
        config = WorkflowConfig.from_yaml_with_cli_overrides(valid_workflow_yaml)
        step = config.steps[index]
        assert isinstance(step, CommandStepConfig)
        assert step.command == expected_command

    def test_parses_step_resources(self, valid_workflow_yaml: Path) -> None:
        config = WorkflowConfig.from_yaml_with_cli_overrides(valid_workflow_yaml)
        assert config.steps[0].resources.memory_gb == 10
        assert config.steps[0].resources.runtime == "01:00:00"
        assert config.steps[1].resources.cores == 2

    def test_parses_step_environment(self, valid_workflow_yaml: Path) -> None:
        config = WorkflowConfig.from_yaml_with_cli_overrides(valid_workflow_yaml)
        assert config.steps[0].environment is None
        assert config.steps[1].environment == "analysis_env"

    def test_routes_to_simulation_step(
        self, tmp_path: Path, valid_model_spec_file: Path, valid_branch_config_file: Path
    ) -> None:
        steps = [
            {
                "name": "sim",
                "type": "simulation",
                "args": {
                    "model_specification": str(valid_model_spec_file),
                    "branch_configuration": str(valid_branch_config_file),
                },
                "resources": {"memory_gb": 5, "runtime": "03:00:00"},
            }
        ]
        workflow_dict = make_workflow_dict(steps=steps)
        yaml_path = write_workflow_yaml(tmp_path, workflow_dict)

        config = WorkflowConfig.from_yaml_with_cli_overrides(yaml_path)
        assert len(config.steps) == 1
        assert isinstance(config.steps[0], SimulationStepConfig)
        assert config.steps[0].name == "sim"


class TestWorkflowConfigValidation:
    """Verify that invalid configurations raise errors."""

    @pytest.mark.parametrize("field", ["name", "steps"])
    def test_rejects_missing_required_field(self, tmp_path: Path, field: str) -> None:
        data = make_workflow_dict()
        del data["workflow"][field]
        yaml_path = write_workflow_yaml(tmp_path, data)
        with pytest.raises(KeyError, match=field):
            WorkflowConfig.from_yaml_with_cli_overrides(yaml_path)

    def test_rejects_empty_steps(self, tmp_path: Path) -> None:
        data = make_workflow_dict(steps=[])
        yaml_path = write_workflow_yaml(tmp_path, data)
        with pytest.raises(KeyError, match="steps"):
            WorkflowConfig.from_yaml_with_cli_overrides(yaml_path)

    def test_rejects_duplicate_step_names(self, tmp_path: Path) -> None:
        steps = [
            make_step_dict(name="dupe"),
            make_step_dict(name="dupe", command="echo hi"),
        ]
        data = make_workflow_dict(steps=steps)
        yaml_path = write_workflow_yaml(tmp_path, data)
        with pytest.raises(ValueError, match="unique"):
            WorkflowConfig.from_yaml_with_cli_overrides(yaml_path)

    def test_rejects_step_without_command(self, tmp_path: Path) -> None:
        steps = [{"name": "no_cmd", "resources": {"memory_gb": 4}}]
        data = make_workflow_dict(steps=steps)
        yaml_path = write_workflow_yaml(tmp_path, data)
        with pytest.raises(KeyError, match="command"):
            WorkflowConfig.from_yaml_with_cli_overrides(yaml_path)

    def test_rejects_step_without_resources(self, tmp_path: Path) -> None:
        steps = [{"name": "no_resources", "command": "echo hello"}]
        data = make_workflow_dict(steps=steps)
        yaml_path = write_workflow_yaml(tmp_path, data)
        with pytest.raises(KeyError, match="resources"):
            WorkflowConfig.from_yaml_with_cli_overrides(yaml_path)

    def test_rejects_missing_workflow_key(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "workflow.yaml"
        yaml_path.write_text("not_workflow:\n  name: oops\n")
        with pytest.raises(KeyError, match="workflow"):
            WorkflowConfig.from_yaml_with_cli_overrides(yaml_path)

    def test_rejects_step_with_both_command_and_type(self, tmp_path: Path) -> None:
        steps = [
            {
                "name": "bad_step",
                "command": "echo hello",
                "type": "simulation",
                "resources": {"memory_gb": 4},
            }
        ]
        data = make_workflow_dict(steps=steps)
        yaml_path = write_workflow_yaml(tmp_path, data)
        with pytest.raises(ValueError, match="Cannot specify both 'command' and 'type'"):
            WorkflowConfig.from_yaml_with_cli_overrides(yaml_path)


class TestWorkflowConfigFromYamlWithCliOverrides:
    """Verify that ``from_yaml_with_cli_overrides`` merges CLI args and validates."""

    @pytest.mark.parametrize(
        "field, value",
        [
            ("project", "proj_simscience_prod"),
            ("queue", "long.q"),
            ("output_directory", Path("/cli/output")),
        ],
    )
    def test_cli_overrides_yaml(
        self, valid_workflow_yaml: Path, field: str, value: str | Path
    ) -> None:
        config = WorkflowConfig.from_yaml_with_cli_overrides(
            valid_workflow_yaml, **{field: value}  # type: ignore[arg-type]
        )
        assert getattr(config, field) == value

    @pytest.mark.parametrize("field", ["project", "queue", "output_directory"])
    def test_rejects_missing_field_everywhere(self, tmp_path: Path, field: str) -> None:
        data = make_workflow_dict()
        del data["workflow"][field]
        yaml_path = write_workflow_yaml(tmp_path, data)
        with pytest.raises(
            ValueError, match=f"(?i){field.replace('_', ' ').split()[0]}.*required"
        ):
            WorkflowConfig.from_yaml_with_cli_overrides(yaml_path)

    @pytest.mark.parametrize(
        "field, cli_value",
        [
            ("project", "proj_simscience"),
            ("queue", "long.q"),
            ("output_directory", Path("/from/cli")),
        ],
    )
    def test_cli_fills_missing_yaml_field(
        self, tmp_path: Path, field: str, cli_value: str | Path
    ) -> None:
        data = make_workflow_dict()
        del data["workflow"][field]
        yaml_path = write_workflow_yaml(tmp_path, data)
        kwargs: dict[str, str | Path | None] = {field: cli_value}
        config = WorkflowConfig.from_yaml_with_cli_overrides(yaml_path, **kwargs)  # type: ignore[arg-type]
        assert getattr(config, field) == cli_value


class TestResourceConfigValidation:
    """Verify ``ResourceConfig`` validation."""

    def test_accepts_valid_runtime(self) -> None:
        rc = ResourceConfig(memory_gb=1, runtime="01:30:00")
        assert rc.runtime == "01:30:00"

    @pytest.mark.parametrize("bad_runtime", ["90m", "1:00:00", "abc"])
    def test_rejects_invalid_runtime(self, bad_runtime: str) -> None:
        with pytest.raises(ValueError, match="hh:mm:ss"):
            ResourceConfig(memory_gb=1, runtime=bad_runtime)

    def test_uses_default_runtime(self) -> None:
        rc = ResourceConfig(memory_gb=1)
        assert rc.runtime == "01:00:00"

    def test_from_dict_defaults(self) -> None:
        rc = ResourceConfig.from_dict({"memory_gb": 4})
        assert rc.memory_gb == 4
        assert rc.runtime == "01:00:00"
        assert rc.cores == 1

    def test_from_dict_all_fields(self) -> None:
        rc = ResourceConfig.from_dict({"memory_gb": 8, "runtime": "02:00:00", "cores": 4})
        assert rc.memory_gb == 8
        assert rc.runtime == "02:00:00"
        assert rc.cores == 4


class TestCommandStepConfig:
    """Tests for CommandStepConfig - the default command-based step type."""

    def test_supported_arguments_returns_none(self) -> None:
        config = CommandStepConfig(
            name="test_step",
            resources=ResourceConfig(memory_gb=4, project="proj_simscience", queue="all.q"),
            command="echo test",
            output_directory=Path("/tmp/results"),
        )
        assert config.supported_arguments is None

    def test_get_tasks_creates_single_task(self) -> None:
        config = CommandStepConfig(
            name="test_step",
            resources=ResourceConfig(memory_gb=4, project="proj_simscience", queue="all.q"),
            command="echo hello world",
            output_directory=Path("/tmp/results"),
        )
        mock_tool = MagicMock()
        mock_template = MagicMock()
        mock_task = MagicMock()
        mock_tool.get_task_template.return_value = mock_template
        mock_template.create_task.return_value = mock_task

        tasks = config.get_tasks(
            mock_tool, env="my_env", build_timestamp="2026_04_24_10_00_00"
        )

        assert tasks == [mock_task]
        mock_template.create_task.assert_called_once_with(
            name="test_step",
            compute_resources={
                "queue": "all.q",
                "project": "proj_simscience",
                "memory": 4.0,
                "runtime": 3600,
                "cores": 1,
                "stdout": "/tmp/results",
                "stderr": "/tmp/results",
            },
            env="my_env",
            command="echo hello world",
        )

    def test_to_dict(self) -> None:
        config = CommandStepConfig(
            name="test_step",
            resources=ResourceConfig(memory_gb=4, project="proj_simscience", queue="all.q"),
            command="echo test",
            output_directory=Path("/tmp/results"),
            environment="my_env",
        )
        result = config.to_dict()
        assert result == {
            "name": "test_step",
            "command": "echo test",
            "resources": {
                "memory_gb": 4,
                "project": "proj_simscience",
                "queue": "all.q",
                "runtime": "01:00:00",
            },
            "environment": "my_env",
        }

    @pytest.mark.parametrize(
        "missing_field,kwargs",
        [
            (
                "name",
                {
                    "resources": ResourceConfig(memory_gb=4),
                    "command": "echo test",
                    "output_directory": Path("/tmp/results"),
                },
            ),
            (
                "resources",
                {
                    "name": "test_step",
                    "command": "echo test",
                    "output_directory": Path("/tmp/results"),
                },
            ),
        ],
        ids=["missing_name", "missing_resources"],
    )
    def test_requires_required_fields(
        self, missing_field: str, kwargs: dict[str, Any]
    ) -> None:
        with pytest.raises(TypeError, match=missing_field):
            CommandStepConfig(**kwargs)


class TestSimulationStepConfig:
    """Tests for SimulationStepConfig - the simulation step type."""

    @pytest.mark.parametrize(
        "omitted_field, provided_field, provided_fixture",
        [
            ("model_specification", "branch_configuration", "valid_branch_config_file"),
            ("branch_configuration", "model_specification", "valid_model_spec_file"),
        ],
    )
    def test_requires_required_args(
        self,
        omitted_field: str,
        provided_field: str,
        provided_fixture: str,
        request: pytest.FixtureRequest,
    ) -> None:
        fixture_value = request.getfixturevalue(provided_fixture)
        with pytest.raises(TypeError, match=omitted_field):
            SimulationStepConfig(
                name="sim",
                resources=ResourceConfig(memory_gb=5),
                output_directory=Path("/tmp/results"),
                **{provided_field: fixture_value},
            )

    def test_accepts_all_fields(
        self,
        valid_model_spec_file: Path,
        valid_branch_config_file: Path,
        valid_artifact_file: Path,
    ) -> None:
        config = SimulationStepConfig(
            name="sim",
            resources=ResourceConfig(
                memory_gb=5,
                hardware=["r650", "r650v2"],
                project="proj_simscience",
                queue="all.q",
            ),
            output_directory=Path("/tmp/results"),
            model_specification=valid_model_spec_file,
            branch_configuration=valid_branch_config_file,
            artifact_path=valid_artifact_file,
        )
        assert config.model_specification == valid_model_spec_file
        assert config.branch_configuration == valid_branch_config_file
        assert config.artifact_path == valid_artifact_file
        assert config.resources.hardware == ["r650", "r650v2"]

    def test_optional_fields_default_to_none(
        self, valid_model_spec_file: Path, valid_branch_config_file: Path
    ) -> None:
        config = SimulationStepConfig(
            name="sim",
            resources=ResourceConfig(memory_gb=5, project="proj_simscience", queue="all.q"),
            output_directory=Path("/tmp/results"),
            model_specification=valid_model_spec_file,
            branch_configuration=valid_branch_config_file,
        )
        assert config.artifact_path is None
        assert config.resources.hardware is None

    def test_from_dict_deserialization(
        self,
        valid_model_spec_file: Path,
        valid_branch_config_file: Path,
    ) -> None:
        step_dict: dict[str, Any] = {
            "name": "sim",
            "type": "simulation",
            "resources": {"memory_gb": 5, "runtime": "03:00:00"},
            "args": {
                "model_specification": str(valid_model_spec_file),
                "branch_configuration": str(valid_branch_config_file),
            },
        }

        config = SimulationStepConfig.from_dict(
            step_dict,
            output_directory=Path("/tmp/results"),
            project="proj_simscience",
            queue="all.q",
        )
        assert isinstance(config, SimulationStepConfig)
        assert config.name == "sim"
        assert config.model_specification == valid_model_spec_file
        assert config.branch_configuration == valid_branch_config_file

    def test_from_dict_rejects_unsupported_args(
        self,
        valid_model_spec_file: Path,
        valid_branch_config_file: Path,
    ) -> None:
        step_dict: dict[str, Any] = {
            "name": "sim",
            "type": "simulation",
            "resources": {"memory_gb": 5},
            "args": {
                "model_specification": str(valid_model_spec_file),
                "branch_configuration": str(valid_branch_config_file),
                "bogus_field": "nope",
            },
        }
        with pytest.raises(ValueError, match="unsupported args"):
            SimulationStepConfig.from_dict(
                step_dict,
                output_directory=Path("/tmp/results"),
                project="proj_simscience",
                queue="all.q",
            )

    def test_to_dict_serialization(
        self,
        valid_model_spec_file: Path,
        valid_branch_config_file: Path,
        valid_artifact_file: Path,
    ) -> None:
        config = SimulationStepConfig(
            name="sim",
            resources=ResourceConfig(
                memory_gb=5,
                runtime="03:00:00",
                hardware=["r650"],
                project="proj_simscience",
                queue="all.q",
            ),
            output_directory=Path("/tmp/results"),
            model_specification=valid_model_spec_file,
            branch_configuration=valid_branch_config_file,
            artifact_path=valid_artifact_file,
        )
        result = config.to_dict()
        assert result["type"] == "simulation"
        assert result["name"] == "sim"
        assert result["args"]["model_specification"] == str(valid_model_spec_file)
        assert result["args"]["branch_configuration"] == str(valid_branch_config_file)
        assert result["args"]["artifact_path"] == str(valid_artifact_file)
        assert result["resources"]["hardware"] == ["r650"]

    def test_to_dict_omits_none_optional_fields(
        self,
        valid_model_spec_file: Path,
        valid_branch_config_file: Path,
    ) -> None:
        config = SimulationStepConfig(
            name="sim",
            resources=ResourceConfig(memory_gb=5, project="proj_simscience", queue="all.q"),
            output_directory=Path("/tmp/results"),
            model_specification=valid_model_spec_file,
            branch_configuration=valid_branch_config_file,
        )
        result = config.to_dict()
        assert "artifact_path" not in result["args"]
        assert "hardware" not in result["resources"]

    def test_get_tasks_wires_arguments(
        self,
        valid_model_spec_file: Path,
        valid_branch_config_file: Path,
        valid_artifact_file: Path,
    ) -> None:
        """Verify get_tasks() passes the right arguments through the pipeline."""
        _cfg = "vivarium_cluster_tools.psimulate.workflow_config.config"
        with (
            patch(f"{_cfg}.OutputPaths") as mock_output_paths_cls,
            patch(f"{_cfg}.branches.Keyspace") as mock_keyspace_cls,
            patch(f"{_cfg}.build_job_parameters_from_keyspace") as mock_build_job_params,
            patch(f"{_cfg}.get_task_list") as mock_get_task_list,
        ):
            # -- Arrange --
            mock_output_paths = MagicMock()
            mock_output_paths.root = Path("/out/root")
            mock_output_paths.worker_logging_root = Path("/out/logs")
            mock_output_paths.backup_dir = Path("/out/backup")
            mock_output_paths.backup_metadata_path = Path("/out/backup_meta.csv")
            mock_output_paths.metadata_dir = Path("/out/metadata")
            mock_output_paths.results_dir = Path("/out/results")
            mock_output_paths_cls.from_entry_point_args.return_value = mock_output_paths

            mock_keyspace = MagicMock()
            mock_keyspace_cls.from_branch_configuration.return_value = mock_keyspace

            sentinel_job_params = [MagicMock(), MagicMock()]
            mock_build_job_params.return_value = sentinel_job_params

            sentinel_tasks = [MagicMock(), MagicMock(), MagicMock()]
            mock_get_task_list.return_value = sentinel_tasks

            config = SimulationStepConfig(
                name="sim_step",
                resources=ResourceConfig(
                    memory_gb=8,
                    runtime="02:00:00",
                    project="proj_simscience",
                    queue="all.q",
                ),
                output_directory=Path("/tmp/results"),
                model_specification=valid_model_spec_file,
                branch_configuration=valid_branch_config_file,
                artifact_path=valid_artifact_file,
                backup_freq=300,
                sim_verbosity=1,
            )

            mock_tool = MagicMock()
            build_ts = "2026_04_24_10_00_00"

            # -- Act --
            result = config.get_tasks(mock_tool, env="test_env", build_timestamp=build_ts)

            # -- Assert: OutputPaths created correctly --
            mock_output_paths_cls.from_entry_point_args.assert_called_once_with(
                command="run",
                input_artifact_path=valid_artifact_file,
                result_directory=Path("/tmp/results"),
                input_model_spec_path=valid_model_spec_file,
                launch_time=build_ts,
                is_resume=False,
            )
            mock_output_paths.touch.assert_called_once()

            # -- Assert: Keyspace parsed from branch config --
            mock_keyspace_cls.from_branch_configuration.assert_called_once_with(
                valid_branch_config_file,
            )

            # -- Assert: job parameters built with correct args --
            mock_build_job_params.assert_called_once()
            call_kwargs = mock_build_job_params.call_args
            assert call_kwargs.args[0] is mock_keyspace
            assert call_kwargs.kwargs["model_specification_path"] == valid_model_spec_file
            assert call_kwargs.kwargs["output_root"] == Path("/out/root")
            assert call_kwargs.kwargs["worker_logging_root"] == Path("/out/logs")
            backup_cfg = call_kwargs.kwargs["backup_configuration"]
            assert backup_cfg["backup_dir"] == str(Path("/out/backup"))
            assert backup_cfg["backup_freq"] == 300
            assert backup_cfg["backup_metadata_path"] == str(Path("/out/backup_meta.csv"))
            assert call_kwargs.kwargs["extras"] == {"sim_verbosity": 1}

            # -- Assert: get_task_list called with pipeline outputs --
            mock_get_task_list.assert_called_once_with(
                tool=mock_tool,
                command="run",
                job_parameters_list=sentinel_job_params,
                metadata_dir=Path("/out/metadata"),
                results_dir=Path("/out/results"),
                worker_logging_root=Path("/out/logs"),
                native_specification=config.native_specification,
                env="test_env",
            )

            # -- Assert: returns whatever get_task_list returns --
            assert result is sentinel_tasks


class TestPytestStepConfig:
    """Tests for PytestStepConfig - the pytest step type."""

    def test_supported_arguments_returns_expected_set(self) -> None:
        config = PytestStepConfig(
            name="tests",
            resources=ResourceConfig(memory_gb=4, project="proj_simscience", queue="all.q"),
            output_directory=Path("/tmp/results"),
            path="tests/",
        )
        assert config.supported_arguments == {"path", "k", "runslow"}

    def test_rejects_neither_path_nor_k(self) -> None:
        with pytest.raises(ValueError, match="at least one of 'path' or 'k'"):
            PytestStepConfig(
                name="tests",
                resources=ResourceConfig(
                    memory_gb=4, project="proj_simscience", queue="all.q"
                ),
                output_directory=Path("/tmp/results"),
            )

    def test_accepts_all_supported_args(self) -> None:
        config = PytestStepConfig(
            name="tests",
            resources=ResourceConfig(
                memory_gb=4, project="proj_simscience", queue="all.q", cores=4
            ),
            output_directory=Path("/tmp/results"),
            path="tests/unit",
            k="not slow",
            runslow=True,
        )
        assert config.path == "tests/unit"
        assert config.k == "not slow"
        assert config.runslow is True

    def test_from_dict_deserialization(self) -> None:
        step_dict = make_pytest_step_dict(
            args={"path": "tests/unit", "k": "test_foo", "runslow": True},
            resources={"memory_gb": 8, "runtime": "02:00:00", "cores": 4},
        )
        config = PytestStepConfig.from_dict(
            step_dict,
            output_directory=Path("/tmp/results"),
            project="proj_simscience",
            queue="all.q",
        )
        assert isinstance(config, PytestStepConfig)
        assert config.name == "run_tests"
        assert config.path == "tests/unit"
        assert config.k == "test_foo"
        assert config.runslow is True
        assert config.resources.cores == 4

    def test_from_dict_rejects_unsupported_args(self) -> None:
        step_dict = make_pytest_step_dict(
            args={"path": "tests/", "bogus_flag": "nope"},
        )
        with pytest.raises(ValueError, match="unsupported args"):
            PytestStepConfig.from_dict(
                step_dict,
                output_directory=Path("/tmp/results"),
                project="proj_simscience",
                queue="all.q",
            )

    def test_to_dict_serialization(self) -> None:
        config = PytestStepConfig(
            name="tests",
            resources=ResourceConfig(
                memory_gb=8, project="proj_simscience", queue="all.q", cores=4
            ),
            output_directory=Path("/tmp/results"),
            path="tests/unit",
            k="test_foo",
            runslow=True,
        )
        result = config.to_dict()
        assert result == {
            "name": "tests",
            "type": "pytest",
            "resources": {
                "memory_gb": 8,
                "runtime": "01:00:00",
                "project": "proj_simscience",
                "queue": "all.q",
                "cores": 4,
            },
            "args": {
                "path": "tests/unit",
                "k": "test_foo",
                "runslow": True,
            },
        }

    def test_to_dict_omits_unset_optional_fields(self) -> None:
        config = PytestStepConfig(
            name="tests",
            resources=ResourceConfig(memory_gb=4, project="proj_simscience", queue="all.q"),
            output_directory=Path("/tmp/results"),
            path="tests/",
        )
        result = config.to_dict()
        assert result["args"] == {"path": "tests/"}

    def test_get_tasks_builds_correct_command(self) -> None:
        config = PytestStepConfig(
            name="tests",
            resources=ResourceConfig(
                memory_gb=4, project="proj_simscience", queue="all.q", cores=4
            ),
            output_directory=Path("/tmp/results"),
            path="tests/unit",
            k="test_foo or test_bar",
            runslow=True,
        )
        mock_tool = MagicMock()
        mock_template = MagicMock()
        mock_task = MagicMock()
        mock_tool.get_task_template.return_value = mock_template
        mock_template.create_task.return_value = mock_task

        tasks = config.get_tasks(
            mock_tool, env="my_env", build_timestamp="2026_05_04_10_00_00"
        )
        assert tasks == [mock_task]
        call_kwargs = mock_template.create_task.call_args[1]
        assert call_kwargs["command"] == (
            "pytest tests/unit -k 'test_foo or test_bar' --runslow --numprocesses 4"
        )

    def test_build_command_path_only(self) -> None:
        config = PytestStepConfig(
            name="tests",
            resources=ResourceConfig(memory_gb=4, project="proj_simscience", queue="all.q"),
            output_directory=Path("/tmp/results"),
            path="tests/",
        )
        assert config._build_command() == "pytest tests/"

    def test_build_command_k_only(self) -> None:
        config = PytestStepConfig(
            name="tests",
            resources=ResourceConfig(memory_gb=4, project="proj_simscience", queue="all.q"),
            output_directory=Path("/tmp/results"),
            k="test_specific",
        )
        assert config._build_command() == "pytest -k test_specific"

    def test_build_command_single_core_omits_numprocesses(self) -> None:
        config = PytestStepConfig(
            name="tests",
            resources=ResourceConfig(memory_gb=4, project="proj_simscience", queue="all.q"),
            output_directory=Path("/tmp/results"),
            path="tests/",
        )
        assert "--numprocesses" not in config._build_command()

    def test_build_command_multiple_paths(self) -> None:
        config = PytestStepConfig(
            name="tests",
            resources=ResourceConfig(memory_gb=4, project="proj_simscience", queue="all.q"),
            output_directory=Path("/tmp/results"),
            path=["tests/unit", "tests/integration"],
        )
        assert config._build_command() == "pytest tests/unit tests/integration"

    def test_to_dict_multiple_paths(self) -> None:
        config = PytestStepConfig(
            name="tests",
            resources=ResourceConfig(memory_gb=4, project="proj_simscience", queue="all.q"),
            output_directory=Path("/tmp/results"),
            path=["tests/unit", "tests/integration"],
        )
        result = config.to_dict()
        assert result["args"] == {"path": ["tests/unit", "tests/integration"]}

    def test_from_dict_multiple_paths(self) -> None:
        step_dict = make_pytest_step_dict(
            args={"path": ["tests/unit", "tests/integration"]},
        )
        config = PytestStepConfig.from_dict(
            step_dict,
            output_directory=Path("/tmp/results"),
            project="proj_simscience",
            queue="all.q",
        )
        assert config.path == ["tests/unit", "tests/integration"]
        assert config._build_command() == "pytest tests/unit tests/integration"

    def test_routes_to_pytest_step_from_yaml(self, tmp_path: Path) -> None:
        steps = [make_pytest_step_dict()]
        workflow_dict = make_workflow_dict(steps=steps)
        yaml_path = write_workflow_yaml(tmp_path, workflow_dict)

        config = WorkflowConfig.from_yaml_with_cli_overrides(yaml_path)
        assert len(config.steps) == 1
        assert isinstance(config.steps[0], PytestStepConfig)
        assert config.steps[0].name == "run_tests"
