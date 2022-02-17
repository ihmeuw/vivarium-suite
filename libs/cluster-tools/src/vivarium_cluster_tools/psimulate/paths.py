"""
====================
File Path Management
====================

"""
from pathlib import Path
from typing import NamedTuple, Optional


DEFAULT_OUTPUT_DIRECTORY = "/share/costeffectiveness/results"


class InputPaths(NamedTuple):
    model_specification: Optional[Path]
    branch_configuration: Optional[Path]
    artifact: Optional[Path]
    result_directory: Path

    @classmethod
    def from_args(
        cls,
        *,  # No positional args allowed.
        result_directory: str,
        input_model_specification_path: str = None,
        input_branch_configuration_path: str = None,
        input_artifact_path: str = None,
    ) -> 'InputPaths':
        if result_directory is None:
            raise ValueError('Result directory must be provided.')
        return InputPaths(
            model_specification=cls._coerce_path(input_model_specification_path),
            branch_configuration=cls._coerce_path(input_branch_configuration_path),
            artifact=cls._coerce_path(input_artifact_path),
            result_directory=cls._coerce_path(result_directory)
        )

    @staticmethod
    def _coerce_path(path: Optional[str]) -> Path:
        if path is not None:
            return Path(path)

