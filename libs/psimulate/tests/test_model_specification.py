from pathlib import Path
from typing import Any

import pytest
import yaml
from vivarium.config_tree.exceptions import ConfigurationError

from vivarium.psimulate import COMMANDS
from vivarium.psimulate.branches import Keyspace
from vivarium.psimulate.model_specification import FULL_ARTIFACT_PATH_KEY, parse


def _write_model_specification(tmp_path: Path, configuration: dict[str, Any]) -> Path:
    path = tmp_path / "model_specification.yaml"
    path.write_text(yaml.dump({"configuration": configuration}))
    return path


@pytest.mark.parametrize(
    "configuration, expected_input_data",
    [
        pytest.param({}, {}, id="no_input_data_section"),
        pytest.param(
            {"input_data": {"location": "Kenya"}},
            {"location": "Kenya"},
            id="input_data_section_without_artifact_path",
        ),
        pytest.param(
            {"input_data": {"location": "Kenya", "artifact_path": "/from/spec.hdf"}},
            {"location": "Kenya"},
            id="input_data_section_with_artifact_path",
        ),
    ],
)
def test_parse_sets_artifact_path_from_cli(
    tmp_path: Path, configuration: dict[str, Any], expected_input_data: dict[str, Any]
) -> None:
    """The command line artifact path lands in ``input_data.artifact_path``.

    This holds whether or not the model specification has an ``input_data``
    section, and the other keys of an existing section are kept.
    """
    artifact_path = tmp_path / "artifact.hdf"

    model_specification = parse(
        command=COMMANDS.run,
        input_model_specification_path=_write_model_specification(tmp_path, configuration),
        artifact_path=artifact_path,
        model_specification_path=tmp_path / "output_model_specification.yaml",
        results_root=tmp_path / "results",
        keyspace=Keyspace(branches=[{}], keyspace={}),
    )

    assert model_specification.configuration.input_data.to_dict() == {
        **expected_input_data,
        "artifact_path": str(artifact_path),
    }


def test_parse_raises_if_artifact_path_from_cli_and_keyspace(tmp_path: Path) -> None:
    artifact_path = tmp_path / "artifact.hdf"

    with pytest.raises(ConfigurationError, match="both in the branch specification"):
        parse(
            command=COMMANDS.run,
            input_model_specification_path=_write_model_specification(tmp_path, {}),
            artifact_path=artifact_path,
            model_specification_path=tmp_path / "output_model_specification.yaml",
            results_root=tmp_path / "results",
            keyspace=Keyspace(
                branches=[{}], keyspace={FULL_ARTIFACT_PATH_KEY: ["/from/branches.hdf"]}
            ),
        )
