import pytest

# jobmon lives in the [cluster] extra and isn't installed on envs without it
# (includeing Github Actions runners).
pytest.importorskip("jobmon")

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pandas as pd
import yaml
from click.testing import CliRunner
from pandas.testing import assert_frame_equal

from tests.psimulate.conftest import make_job_parameters
from vivarium.cluster_tools.psimulate.cli import psimulate
from vivarium.cluster_tools.psimulate.cluster.interface import NativeSpecification
from vivarium.cluster_tools.psimulate.jobs import generate_task_id
from vivarium.cluster_tools.psimulate.paths import InputPaths
from vivarium.cluster_tools.psimulate.runner import (
    report_initial_status,
    write_backup_metadata,
    write_configuration,
)

_RUNNER_MAIN = "vivarium.cluster_tools.psimulate.runner.main"


def _write_yaml(tmp_path: Path, data: dict[str, Any], name: str = "config.yaml") -> Path:
    p = tmp_path / name
    p.write_text(yaml.dump(data))
    return p


def _make_native_spec(**overrides: Any) -> NativeSpecification:
    """Create a NativeSpecification with sensible defaults, overridable by keyword."""
    defaults: dict[str, Any] = {
        "job_name": "my_sim",
        "project": "proj_simscience",
        "queue": "all.q",
        "peak_memory": 4,
        "max_runtime": "01:00:00",
        "hardware": [],
    }
    defaults.update(overrides)
    return NativeSpecification(**defaults)


def _make_run_input_paths(**overrides: Any) -> InputPaths:
    """Create InputPaths suitable for a ``run`` command."""
    defaults: dict[str, Any] = {
        "model_specification": Path("/data/model_spec.yaml"),
        "branch_configuration": Path("/data/branches.yaml"),
        "artifact": None,
        "result_directory": Path("/results/output"),
    }
    defaults.update(overrides)
    return InputPaths(**defaults)


def _make_restart_input_paths(**overrides: Any) -> InputPaths:
    """Create InputPaths suitable for ``restart`` / ``expand`` commands."""
    defaults: dict[str, Any] = {
        "model_specification": None,
        "branch_configuration": None,
        "artifact": None,
        "result_directory": Path("/results/previous_run"),
    }
    defaults.update(overrides)
    return InputPaths(**defaults)


def _read_configuration_yaml(output_root: Path) -> dict[str, Any]:
    """Read and parse the configuration.yaml written by write_configuration."""
    config_file = output_root / "configuration.yaml"
    assert config_file.exists()
    result: dict[str, Any] = yaml.safe_load(config_file.read_text())
    return result


def test_report_initial_status() -> None:
    number_existing_jobs = 10
    finished_sim_metadata = pd.DataFrame(index=range(number_existing_jobs))
    report_initial_status(number_existing_jobs, finished_sim_metadata, 100)
    with pytest.raises(RuntimeError, match="There are 1 jobs from the previous run"):
        report_initial_status(number_existing_jobs + 1, finished_sim_metadata, 100)


def test_write_backup_metadata(tmp_path: Path) -> None:
    metadata_path = tmp_path / "metadata.csv"
    job_parameters_list = [
        make_job_parameters(
            branch_configuration={"category": {"detail": 9}},
            input_draw=1337,
            random_seed=42,
        ),
    ]
    expected_task_id_1 = generate_task_id(1337, 42, {"category": {"detail": 9}})
    write_backup_metadata(metadata_path, job_parameters_list)
    assert metadata_path.exists()
    metadata = pd.read_csv(metadata_path)
    expected_df = pd.DataFrame(
        {
            "input_draw": [1337],
            "random_seed": [42],
            "job_id": [expected_task_id_1],
            "category.detail": [9],
        }
    )
    assert_frame_equal(metadata, expected_df)

    # Check that we append to the existing metadata
    # upon second execution
    append_job_parameters_list = [
        make_job_parameters(
            branch_configuration={"category": {"detail": 10}},
            input_draw=1338,
            random_seed=43,
        ),
    ]
    expected_task_id_2 = generate_task_id(1338, 43, {"category": {"detail": 10}})
    write_backup_metadata(metadata_path, append_job_parameters_list)
    metadata = pd.read_csv(metadata_path)
    expected_df = pd.DataFrame(
        {
            "input_draw": [1337, 1338],
            "random_seed": [42, 43],
            "job_id": [expected_task_id_1, expected_task_id_2],
            "category.detail": [9, 10],
        }
    )
    assert_frame_equal(metadata, expected_df)


def test_write_configuration_run_command(tmp_path: Path) -> None:
    """Verify that write_configuration produces a configuration.yaml with
    all resolved values for a 'run' command."""
    native_spec = _make_native_spec(
        peak_memory=8,
        max_runtime="04:30:00",
        hardware=["r650", "r650xs"],
    )
    input_paths = _make_run_input_paths()

    write_configuration(
        output_root=tmp_path,
        command="run",
        input_paths=input_paths,
        native_specification=native_spec,
        max_workers=500,
        max_attempts=5,
        backup_freq=1800,
        extra_args={"sim_verbosity": 1},
    )

    config = _read_configuration_yaml(tmp_path)
    assert config["model_specification"] == "/data/model_spec.yaml"
    assert config["branch_configuration"] == "/data/branches.yaml"
    assert config["result_directory"] == "/results/output"
    assert "artifact_path" not in config
    assert "results_root" not in config
    assert config["project"] == "proj_simscience"
    assert config["queue"] == "all.q"
    assert config["peak_memory"] == 8
    assert config["max_runtime"] == "04:30:00"
    assert config["hardware"] == "r650,r650xs"
    assert config["max_workers"] == 500
    assert config["max_attempts"] == 5
    assert config["backup_freq"] == "30.0"
    assert config["sim_verbosity"] == "1"


def test_write_configuration_restart_command(tmp_path: Path) -> None:
    """Verify that write_configuration uses results_root for restart."""
    write_configuration(
        output_root=tmp_path,
        command="restart",
        input_paths=_make_restart_input_paths(),
        native_specification=_make_native_spec(),
        max_workers=100,
        max_attempts=3,
        backup_freq=None,
        extra_args={"sim_verbosity": 0},
    )

    config = _read_configuration_yaml(tmp_path)
    assert config["results_root"] == "/results/previous_run"
    assert "model_specification" not in config
    assert "result_directory" not in config
    assert "hardware" not in config
    assert "backup_freq" not in config


def test_write_configuration_expand_command(tmp_path: Path) -> None:
    """Verify that write_configuration includes add_draws/add_seeds for expand."""
    write_configuration(
        output_root=tmp_path,
        command="expand",
        input_paths=_make_restart_input_paths(),
        native_specification=_make_native_spec(),
        max_workers=100,
        max_attempts=3,
        backup_freq=None,
        extra_args={"sim_verbosity": 0, "num_draws": 5, "num_seeds": 3},
    )

    config = _read_configuration_yaml(tmp_path)
    assert config["results_root"] == "/results/previous_run"
    assert config["add_draws"] == 5
    assert config["add_seeds"] == 3


def test_write_configuration_cli_and_config(tmp_path: Path) -> None:
    """Verify that values from a combination of CLI args and a config file
    are captured correctly in the configuration.yaml file.

    The config file supplies baseline values; CLI flags override some of them.
    We assert the *actually-used* values end up in the configuration file.
    """
    model_spec = tmp_path / "model_spec.yaml"
    model_spec.write_text("components: {}\n")
    branch_config = tmp_path / "branches.yaml"
    branch_config.write_text("input_draw_count: 1\nrandom_seed_count: 1\n")
    result_dir = tmp_path / "results"
    result_dir.mkdir()

    # Config file provides baseline resource values
    config = _write_yaml(
        tmp_path,
        {
            "model_specification": str(model_spec),
            "branch_configuration": str(branch_config),
            "result_directory": str(result_dir),
            "project": "proj_simscience",
            "peak_memory": 4,
            "max_runtime": "02:00:00",
            "queue": "all.q",
            "max_workers": 200,
            "max_attempts": 2,
            "hardware": "r650",
        },
    )

    # CLI overrides peak_memory and max_workers
    cli_args = [
        "run",
        "--run-config",
        str(config),
        "--peak-memory",
        "16",
        "--max-workers",
        "1000",
    ]

    cli_runner = CliRunner()
    with patch(_RUNNER_MAIN) as mock_main:
        result = cli_runner.invoke(psimulate, cli_args)

    assert result.exit_code == 0, result.output
    mock_main.assert_called_once()
    call_kwargs = mock_main.call_args.kwargs
    native_spec = call_kwargs["native_specification"]

    # Now write the configuration file using the values the CLI resolved
    output_dir = tmp_path / "config_check"
    output_dir.mkdir()
    write_configuration(
        output_root=output_dir,
        command="run",
        input_paths=call_kwargs["input_paths"],
        native_specification=native_spec,
        max_workers=call_kwargs["max_workers"],
        max_attempts=call_kwargs["max_attempts"],
        backup_freq=call_kwargs["backup_freq"],
        extra_args=call_kwargs["extra_args"],
    )

    config_file = output_dir / "configuration.yaml"
    assert config_file.exists()

    written_config = yaml.safe_load(config_file.read_text())
    # CLI overrides
    assert written_config["peak_memory"] == 16
    assert written_config["max_workers"] == 1000
    # Config file values (not overridden by CLI)
    assert written_config["project"] == "proj_simscience"
    assert written_config["queue"] == "all.q"
    assert written_config["max_runtime"] == "02:00:00"
    assert written_config["max_attempts"] == 2
    assert written_config["hardware"] == "r650"


def test_reuse_configuration_yaml(tmp_path: Path) -> None:
    """Verify that the configuration.yaml produced by a run can be fed back
    to ``psimulate run --run-config`` and yields identical resolved values."""
    model_spec = tmp_path / "model_spec.yaml"
    model_spec.write_text("components: {}\n")
    branch_config = tmp_path / "branches.yaml"
    branch_config.write_text("input_draw_count: 1\nrandom_seed_count: 1\n")
    result_dir = tmp_path / "results"
    result_dir.mkdir()

    native_spec = _make_native_spec(
        peak_memory=8,
        max_runtime="04:30:00",
        hardware=["r650"],
    )
    input_paths = _make_run_input_paths(
        model_specification=model_spec.resolve(),
        branch_configuration=branch_config.resolve(),
        result_directory=result_dir.resolve(),
    )

    # Step 1: Write configuration.yaml as the runner would after a run.
    output_dir = tmp_path / "run_output"
    output_dir.mkdir()
    write_configuration(
        output_root=output_dir,
        command="run",
        input_paths=input_paths,
        native_specification=native_spec,
        max_workers=500,
        max_attempts=5,
        backup_freq=1800,
        extra_args={"sim_verbosity": 1},
    )

    generated_config = output_dir / "configuration.yaml"
    assert generated_config.exists()

    # Step 2: Use the generated configuration.yaml as --run-config for a new run.
    cli_runner = CliRunner()
    with patch(_RUNNER_MAIN) as mock_main:
        result = cli_runner.invoke(psimulate, ["run", "--run-config", str(generated_config)])

    assert result.exit_code == 0, result.output
    mock_main.assert_called_once()
    call_kwargs = mock_main.call_args.kwargs

    # Step 3: Verify the resolved values match what was originally written.
    assert call_kwargs["input_paths"].model_specification == model_spec.resolve()
    assert call_kwargs["input_paths"].branch_configuration == branch_config.resolve()
    assert call_kwargs["input_paths"].result_directory == result_dir.resolve()
    assert call_kwargs["native_specification"].project == "proj_simscience"
    assert call_kwargs["native_specification"].queue == "all.q"
    assert call_kwargs["native_specification"].peak_memory == 8
    assert call_kwargs["native_specification"].max_runtime == "04:30:00"
    assert call_kwargs["native_specification"].hardware == ["r650"]
    assert call_kwargs["max_workers"] == 500
    assert call_kwargs["max_attempts"] == 5
    assert call_kwargs["extra_args"]["sim_verbosity"] == 1
