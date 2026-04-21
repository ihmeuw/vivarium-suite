"""Unit tests for the workflow configuration parser."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.psimulate.workflow_config.utilities import (
    make_step_dict,
    make_workflow_dict,
    write_workflow_yaml,
)
from vivarium_cluster_tools.psimulate.workflow_config.config import (
    ResourceConfig,
    StepConfig,
    WorkflowConfig,
)


class TestWorkflowConfigFromYaml:
    """Verify that ``WorkflowConfig.from_yaml`` correctly parses valid YAML."""

    def test_parses_name(self, valid_workflow_yaml: Path) -> None:
        config = WorkflowConfig.from_yaml(valid_workflow_yaml)
        assert config.name == "test_pipeline"

    def test_parses_project(self, valid_workflow_yaml: Path) -> None:
        config = WorkflowConfig.from_yaml(valid_workflow_yaml)
        assert config.project == "proj_simscience"

    def test_parses_queue(self, valid_workflow_yaml: Path) -> None:
        config = WorkflowConfig.from_yaml(valid_workflow_yaml)
        assert config.queue == "all.q"

    def test_parses_output_directory(self, valid_workflow_yaml: Path) -> None:
        config = WorkflowConfig.from_yaml(valid_workflow_yaml)
        assert config.output_directory == Path("/tmp/results")

    def test_parses_default_environment(self, tmp_path: Path) -> None:
        data = make_workflow_dict(default_environment="my_env")
        yaml_path = write_workflow_yaml(tmp_path, data)
        config = WorkflowConfig.from_yaml(yaml_path)
        assert config.default_environment == "my_env"

    def test_parses_default_environment_absent(self, valid_workflow_yaml: Path) -> None:
        config = WorkflowConfig.from_yaml(valid_workflow_yaml)
        assert config.default_environment is None

    def test_parses_steps_count(self, valid_workflow_yaml: Path) -> None:
        config = WorkflowConfig.from_yaml(valid_workflow_yaml)
        assert len(config.steps) == 2

    def test_step_ordering_preserved(self, valid_workflow_yaml: Path) -> None:
        config = WorkflowConfig.from_yaml(valid_workflow_yaml)
        assert config.steps[0].name == "pre_tests"
        assert config.steps[1].name == "post_analysis"

    def test_parses_command_step(self, valid_workflow_yaml: Path) -> None:
        config = WorkflowConfig.from_yaml(valid_workflow_yaml)
        step = config.steps[0]
        assert step.command == "pytest tests/test_lbwsg.py tests/test_mortality.py --runslow"

    def test_parses_raw_command_step(self, valid_workflow_yaml: Path) -> None:
        config = WorkflowConfig.from_yaml(valid_workflow_yaml)
        step = config.steps[1]
        assert step.command == "python scripts/analyze.py --input /results"

    def test_parses_step_resources(self, valid_workflow_yaml: Path) -> None:
        config = WorkflowConfig.from_yaml(valid_workflow_yaml)
        res = config.steps[0].resources
        assert res is not None
        assert res.memory_gb == 10
        assert res.runtime == "01:00:00"

    def test_parses_step_resources_cores(self, valid_workflow_yaml: Path) -> None:
        config = WorkflowConfig.from_yaml(valid_workflow_yaml)
        res = config.steps[1].resources
        assert res is not None
        assert res.cores == 2

    def test_parses_step_environment(self, valid_workflow_yaml: Path) -> None:
        config = WorkflowConfig.from_yaml(valid_workflow_yaml)
        assert config.steps[1].environment == "analysis_env"

    def test_step_environment_defaults_to_none(self, valid_workflow_yaml: Path) -> None:
        config = WorkflowConfig.from_yaml(valid_workflow_yaml)
        assert config.steps[0].environment is None


class TestWorkflowConfigValidation:
    """Verify that invalid configurations raise ``KeyError``."""

    def test_rejects_missing_name(self, tmp_path: Path) -> None:
        data = make_workflow_dict()
        del data["workflow"]["name"]
        yaml_path = write_workflow_yaml(tmp_path, data)
        with pytest.raises(KeyError, match="name"):
            WorkflowConfig.from_yaml(yaml_path)

    def test_rejects_missing_project(self, tmp_path: Path) -> None:
        data = make_workflow_dict()
        del data["workflow"]["project"]
        yaml_path = write_workflow_yaml(tmp_path, data)
        with pytest.raises(KeyError, match="project"):
            WorkflowConfig.from_yaml(yaml_path)

    def test_rejects_missing_queue(self, tmp_path: Path) -> None:
        data = make_workflow_dict()
        del data["workflow"]["queue"]
        yaml_path = write_workflow_yaml(tmp_path, data)
        with pytest.raises(KeyError, match="queue"):
            WorkflowConfig.from_yaml(yaml_path)

    def test_rejects_missing_output_directory(self, tmp_path: Path) -> None:
        data = make_workflow_dict()
        del data["workflow"]["output_directory"]
        yaml_path = write_workflow_yaml(tmp_path, data)
        with pytest.raises(KeyError, match="output_directory"):
            WorkflowConfig.from_yaml(yaml_path)

    def test_rejects_missing_steps(self, tmp_path: Path) -> None:
        data = make_workflow_dict()
        del data["workflow"]["steps"]
        yaml_path = write_workflow_yaml(tmp_path, data)
        with pytest.raises(KeyError, match="steps"):
            WorkflowConfig.from_yaml(yaml_path)

    def test_rejects_empty_steps(self, tmp_path: Path) -> None:
        data = make_workflow_dict(steps=[])
        yaml_path = write_workflow_yaml(tmp_path, data)
        with pytest.raises(KeyError, match="steps"):
            WorkflowConfig.from_yaml(yaml_path)

    def test_rejects_duplicate_step_names(self, tmp_path: Path) -> None:
        steps = [
            make_step_dict(name="dupe"),
            make_step_dict(name="dupe", command="echo hi"),
        ]
        data = make_workflow_dict(steps=steps)
        yaml_path = write_workflow_yaml(tmp_path, data)
        with pytest.raises(KeyError, match="unique"):
            WorkflowConfig.from_yaml(yaml_path)

    def test_rejects_step_without_command(self, tmp_path: Path) -> None:
        steps = [{"name": "no_cmd", "resources": {"memory_gb": 4}}]
        data = make_workflow_dict(steps=steps)
        yaml_path = write_workflow_yaml(tmp_path, data)
        with pytest.raises(KeyError, match="command"):
            WorkflowConfig.from_yaml(yaml_path)

    def test_rejects_step_without_resources(self, tmp_path: Path) -> None:
        steps = [
            {
                "name": "no_resources",
                "command": "echo hello",
            }
        ]
        data = make_workflow_dict(steps=steps)
        yaml_path = write_workflow_yaml(tmp_path, data)
        with pytest.raises(KeyError, match="resources"):
            WorkflowConfig.from_yaml(yaml_path)

    def test_rejects_missing_workflow_key(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "workflow.yaml"
        yaml_path.write_text("not_workflow:\n  name: oops\n")
        with pytest.raises(KeyError, match="workflow"):
            WorkflowConfig.from_yaml(yaml_path)


class TestResourceConfigValidation:
    """Verify ``ResourceConfig`` validation."""

    def test_accepts_valid_runtime(self) -> None:
        rc = ResourceConfig(memory_gb=1, runtime="01:30:00")
        assert rc.runtime == "01:30:00"

    def test_rejects_invalid_runtime_format(self) -> None:
        with pytest.raises(ValueError, match="hh:mm:ss"):
            ResourceConfig(memory_gb=1, runtime="90m")

    def test_rejects_runtime_missing_leading_zeros(self) -> None:
        with pytest.raises(ValueError, match="hh:mm:ss"):
            ResourceConfig(memory_gb=1, runtime="1:00:00")

    def test_from_dict_defaults(self) -> None:
        rc = ResourceConfig.from_dict({"memory_gb": 4})
        assert rc is not None
        assert rc.memory_gb == 4
        assert rc.runtime == "01:00:00"
        assert rc.cores == 1

    def test_from_dict_all_fields(self) -> None:
        rc = ResourceConfig.from_dict({"memory_gb": 8, "runtime": "02:00:00", "cores": 4})
        assert rc is not None
        assert rc.memory_gb == 8
        assert rc.runtime == "02:00:00"
        assert rc.cores == 4
