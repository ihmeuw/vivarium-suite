"""
====================
File Path Management
====================

"""
from datetime import datetime
from pathlib import Path
from typing import NamedTuple, Optional, Union

from vivarium.interface.utilities import get_output_model_name_string

from vivarium_cluster_tools import utilities as vct_utils
from vivarium_cluster_tools.psimulate import COMMANDS

DEFAULT_OUTPUT_DIRECTORY = "/share/costeffectiveness/results"


class InputPaths(NamedTuple):
    model_specification: Union[None, Path]
    branch_configuration: Union[None, Path]
    artifact: Union[None, Path]
    result_directory: Path

    @classmethod
    def from_entry_point_args(
        cls,
        *,  # No positional args allowed.
        result_directory: Union[str, Path],
        input_model_specification_path: Union[str, Path] = None,
        input_branch_configuration_path: Union[str, Path] = None,
        input_artifact_path: Union[str, Path] = None,
    ) -> "InputPaths":
        if result_directory is None:
            raise ValueError("Result directory must be provided.")
        return InputPaths(
            model_specification=cls._coerce_path(input_model_specification_path),
            branch_configuration=cls._coerce_path(input_branch_configuration_path),
            artifact=cls._coerce_path(input_artifact_path),
            result_directory=cls._coerce_path(result_directory),
        )

    @staticmethod
    def _coerce_path(path: Optional[str]) -> Union[None, Path]:
        if path is not None:
            return Path(path)


class OutputPaths(NamedTuple):
    # Directories
    root: Path

    logging_root: Path
    cluster_logging_root: Path
    worker_logging_root: Path

    # Files
    # Environment configuration
    worker_settings: Path
    environment_file: Path

    # Simulation configuration
    model_specification: Path
    keyspace: Path
    branches: Path

    # outputs
    results: Path

    @classmethod
    def from_entry_point_args(
        cls,
        *,
        command: str,
        input_artifact_path: Optional[Path],
        result_directory: Path,
        input_model_spec_path: Path,
    ) -> "OutputPaths":
        launch_time = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")

        output_directory = result_directory
        if command == COMMANDS.run:
            model_name = get_output_model_name_string(
                input_artifact_path, input_model_spec_path
            )
            output_directory = output_directory / model_name / launch_time
        elif command == COMMANDS.load_test:
            output_directory = output_directory / launch_time

        logging_directory = output_directory / "logs" / f"{launch_time}_{command}"
        logging_dirs = {
            "logging_root": logging_directory,
            "cluster_logging_root": logging_directory / "cluster_logs",
            "worker_logging_root": logging_directory / "worker_logs",
        }

        output_paths = OutputPaths(
            root=output_directory,
            **logging_dirs,
            worker_settings=output_directory / "settings.py",
            environment_file=output_directory / "requirements.txt",
            model_specification=output_directory / "model_specification.yaml",
            keyspace=output_directory / "keyspace.yaml",
            branches=output_directory / "branches.yaml",
            results=output_directory / "output.hdf",
        )
        return output_paths

    def touch(self) -> None:
        vct_utils.mkdir(self.root, exists_ok=True, parents=True)
        for d in [self.logging_root, self.cluster_logging_root, self.worker_logging_root]:
            vct_utils.mkdir(d, parents=True)
