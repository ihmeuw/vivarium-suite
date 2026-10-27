"""
===========================
Interface Utility Functions
===========================

The functions defined here are used to support the command-line interface for
``vivarium``.

"""
from datetime import datetime
from pathlib import Path

import yaml


def get_output_model_name_string(
    artifact_path: str | Path | None,
    model_spec_path: str | Path,
) -> str:
    """Find a good string to use as model name in output path creation.

    Parameters
    ----------
    artifact_path
        Path to the artifact file, if exists, else should be None
    model_spec_path
        Path to the model specification file. This must exist.

    Returns
    -------
        A model name string for use in output labeling.
    """
    if artifact_path:
        model_name = Path(artifact_path).stem
    else:
        with open(model_spec_path) as model_spec_file:
            model_spec = yaml.safe_load(model_spec_file)
        try:
            model_name = Path(model_spec["configuration"]["input_data"]["artifact_path"]).stem
        except KeyError:
            model_name = Path(model_spec_path).stem
    return model_name


def get_output_root(
    results_directory: str | Path,
    model_specification_file: str | Path,
    artifact_path: str | Path,
) -> Path:
    """Create a root directory for output files.

    Parameters
    ----------
    results_directory
        Directory to store the results in.
    model_specification_file
        Path to the model specification file.
    artifact_path
        Path to the artifact file.

    Returns
    -------
        The date-stamped output root directory.
    """
    launch_time = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    model_name = get_output_model_name_string(artifact_path, model_specification_file)
    output_root = Path(results_directory) / model_name / launch_time
    return output_root
