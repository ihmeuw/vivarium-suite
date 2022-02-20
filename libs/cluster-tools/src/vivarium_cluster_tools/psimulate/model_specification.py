"""
==============================
Model specification management
==============================

"""
import typing
from pathlib import Path
from typing import Optional

import yaml
from vivarium.framework.artifact import parse_artifact_path_config
from vivarium.framework.configuration import (
    ConfigTree,
    ConfigurationError,
    build_model_specification,
)

from vivarium_cluster_tools.psimulate import COMMANDS

if typing.TYPE_CHECKING:
    # Cyclic import.
    from vivarium_cluster_tools.psimulate.branches import Keyspace


# Configuration key constants
INPUT_DATA_KEY = "input_data"
ARTIFACT_PATH_KEY = "artifact_path"
FULL_ARTIFACT_PATH_KEY = f"{INPUT_DATA_KEY}.{ARTIFACT_PATH_KEY}"


def parse(
    command: str,
    input_model_specification_path: Optional[Path],
    artifact_path: Optional[Path],
    model_specification_path: Path,
    keyspace: "Keyspace",
) -> ConfigTree:
    if command in [COMMANDS.restart, COMMANDS.expand]:
        return build_model_specification(model_specification_path)
    if command == COMMANDS.load_test:
        return build_model_specification()

    model_specification = build_model_specification(input_model_specification_path)

    artifact_path_is_cli_arg = artifact_path.is_file()
    artifact_path_in_keyspace = FULL_ARTIFACT_PATH_KEY in keyspace

    if artifact_path_is_cli_arg and artifact_path_in_keyspace:
        raise ConfigurationError(
            "Artifact path cannot be specified both in the branch specification file"
            " and as a command line argument.",
            str(artifact_path),
        )
    elif artifact_path_is_cli_arg:
        model_specification.configuration[INPUT_DATA_KEY].update(
            {ARTIFACT_PATH_KEY: str(artifact_path)}, source=__file__
        )
    else:
        # Artifact path comes from the model spec.
        # Parsing here ensures the key exists and the value points
        # to an actual file.
        parse_artifact_path_config(model_specification.configuration)

    return model_specification


def persist(
    model_specification: ConfigTree,
    model_specification_path: Path,
) -> None:
    model_specification_path.write_text(yaml.dump(model_specification.to_dict()))
