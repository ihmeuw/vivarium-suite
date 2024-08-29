"""
====================
File Path Management
====================

"""

from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import NamedTuple, Optional, Union

from vivarium.interface.utilities import get_output_model_name_string

from vivarium_cluster_tools import utilities as vct_utils
from vivarium_cluster_tools.psimulate import COMMANDS

DEFAULT_LOAD_TESTS_DIR = "/mnt/team/simulation_science/priv/engineering/load_tests"
CENTRAL_PERFORMANCE_LOGS_DIRECTORY = Path(
    "/mnt/team/simulation_science/pub/performance_logs/"
)


class InputPaths(NamedTuple):
    """Container class for input filepaths.

    In addition to the path attributes, this class provides a method to create
    the required directories.

    """

    model_specification: Optional[Path]
    """The path to the model specification file."""
    branch_configuration: Optional[Path]
    """The path to the branch configuration file."""
    artifact: Optional[Path]
    """The path to the data artifact."""
    result_directory: Path
    """The path to the results directory."""

    @classmethod
    def from_entry_point_args(
        cls,
        *,  # No positional args allowed.
        result_directory: Union[str, Path],
        input_model_specification_path: Optional[Union[str, Path]] = None,
        input_branch_configuration_path: Optional[Union[str, Path]] = None,
        input_artifact_path: Optional[Union[str, Path]] = None,
    ) -> "InputPaths":
        """Create an instance of InputPaths from the arguments passed to the entry point.

        Parameters
        ----------
        result_directory
            The path to the results directory.
        input_model_specification_path
            The path to the model specification file.
        input_branch_configuration_path
            The path to the branch configuration file.
        input_artifact_path
            The path to the data artifact.

        Returns
        -------
            An instance of InputPaths.

        """
        if result_directory is None:
            raise ValueError("Result directory must be provided.")
        return InputPaths(
            model_specification=cls._coerce_path(input_model_specification_path),
            branch_configuration=cls._coerce_path(input_branch_configuration_path),
            artifact=cls._coerce_path(input_artifact_path),
            result_directory=cls._coerce_path(result_directory),
        )

    @staticmethod
    def _coerce_path(path: Optional[str]) -> Optional[Path]:
        if path is not None:
            return Path(path)


class OutputPaths(NamedTuple):
    """Container class for output filepaths.

    In addition to the path attributes, this class provides a method to create
    the required directories as well as a class method to create an instance
    from the arguments passed to the entry point.

    """

    # Directories
    root: Path
    """The timestamped root directory for the simulation output."""

    logging_root: Path
    """The parent directory for all logs."""
    cluster_logging_root: Path
    """The root directory for cluster logs."""
    worker_logging_root: Path
    """The root directory for worker logs."""

    # Files
    # Environment configuration
    worker_settings: Path
    """The path to the worker settings file."""
    environment_file: Path
    """The path to the requirements.txt environment file."""

    # Simulation configuration
    model_specification: Path
    """The path to the model specification file."""
    keyspace: Path
    """The path to the simulation keyspace file."""
    branches: Path
    """The path to the simulation branches file."""

    # outputs
    finished_sim_metadata: Path
    """The path to the finished simulation metadata file."""
    results_dir: Path
    """The path to the results directory."""
    backup_dir: Path
    """The path to the simulation backup directory."""
    backup_metadata_path: Path
    """The path to the backup metadata file."""

    # will not be reliable if we parallelized across artifacts
    @property
    def artifact_name(self) -> str:
        """Name of the artifact."""
        return self.root.parent.stem

    @property
    def run_date(self) -> str:
        """Date of the simulation run."""
        runtime_info = self.logging_root.stem
        run_date = runtime_info[: runtime_info.rindex("_")]
        return run_date

    @property
    def run_type(self) -> str:
        """Type of the simulation run."""
        runtime_info = self.logging_root.stem
        run_type = runtime_info[runtime_info.rindex("_") + 1 :]
        return run_type

    @property
    def original_run_date(self) -> str:
        """Date of the original simulation run."""
        return self.root.stem

    @property
    def project_name(self) -> str:
        """Name of the project."""
        if self.logging_to_central_results_directory:
            return self.root.parents[3].stem
        else:
            return self.root.parents[1].stem

    @property
    def root_path(self) -> str:
        """Path to the root directory."""
        return self.root.parent

    @property
    def logging_to_central_results_directory(self) -> bool:
        """Whether the logs are being written to the central results directory."""
        return fnmatch(str(self.root), "/mnt/team/simulation_science/pub/models/*/results/*")

    @classmethod
    def from_entry_point_args(
        cls,
        *,  # No positional args allowed.
        command: str,
        input_artifact_path: Optional[Path],
        result_directory: Path,
        input_model_spec_path: Path,
    ) -> "OutputPaths":
        """Create an instance of OutputPaths from the arguments passed to the entry point.

        Parameters
        ----------
        command
            The specific ``psimulate`` command being run.
        input_artifact_path
            The path to the data artifact.
        result_directory
            The path to the results directory.
        input_model_spec_path
            The path to the model specification file.

        Returns
        -------
            An instance of OutputPaths.

        """
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
            finished_sim_metadata=output_directory / "finished_sim_metadata.csv",
            results_dir=output_directory / "results",
            backup_dir=output_directory / "sim_backups",
            backup_metadata_path=output_directory / "sim_backups" / "backup_metadata.csv",
        )
        return output_paths

    def touch(self) -> None:
        """Create the required directories."""
        for dir in [self.root, self.results_dir, self.backup_dir]:
            vct_utils.mkdir(dir, exists_ok=True, parents=True)
        for dir in [self.logging_root, self.cluster_logging_root, self.worker_logging_root]:
            vct_utils.mkdir(dir, parents=True)
