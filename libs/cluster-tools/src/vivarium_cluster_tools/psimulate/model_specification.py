"""
==============================
Model specification management
==============================

"""
import typing
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger
from vivarium.config_tree import ConfigurationKeyError
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
OUTPUT_DATA_KEY = "output_data"
RESULTS_DIRECTORY_KEY = "results_directory"


def parse(
    command: str,
    input_model_specification_path: Optional[Path],
    artifact_path: Optional[Path],
    model_specification_path: Path,
    results_root: Path,
    keyspace: "Keyspace",
) -> ConfigTree:
    if command in [COMMANDS.restart, COMMANDS.expand]:
        return build_model_specification(model_specification_path)
    if command == COMMANDS.load_test:
        return build_model_specification()

    model_specification = build_model_specification(input_model_specification_path)
    model_specification.configuration.update(
        {OUTPUT_DATA_KEY: {RESULTS_DIRECTORY_KEY: str(results_root)}},
        source=__file__,
    )

    artifact_path_is_cli_arg = artifact_path is not None
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
        # Artifact path (if any) comes from the model spec.
        # The framework does not require an artifact, log a debug message in case this
        # is unintentional...
        try:
            parse_artifact_path_config(model_specification.configuration)
        except ConfigurationKeyError:
            logger.debug(
                "No artifact detected in arguments or configuration. This may"
                " be intentional. If not, supply one with the `-i` flag or in"
                " the configuration yaml."
            )

    return model_specification


def persist(
    model_specification: ConfigTree,
    model_specification_path: Path,
) -> None:
    model_specification_path.write_text(
        yaml.dump(model_specification.to_dict(), sort_keys=False)
    )
