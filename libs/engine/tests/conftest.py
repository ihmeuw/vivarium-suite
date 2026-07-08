from __future__ import annotations

import warnings
from collections.abc import Generator
from pathlib import Path

import pytest
import pytest_mock
import yaml
from _pytest.logging import LogCaptureFixture
from loguru import logger
from vivarium.artifact import Artifact
from vivarium.config_tree import ConfigTree
from vivarium.testing_utils import FuzzyChecker

from vivarium.engine.framework.configuration import (
    build_model_specification,
    build_simulation_configuration,
)
from vivarium.engine.framework.engine import SimulationContext
from vivarium.engine.testing_utilities import metadata


@pytest.fixture(autouse=True)
def _clear_simulation_context_cache() -> None:
    SimulationContext._clear_context_cache()


@pytest.fixture(scope="session")
def fuzzy_checker() -> FuzzyChecker:
    return FuzzyChecker()


@pytest.fixture
def caplog(caplog: LogCaptureFixture) -> Generator[LogCaptureFixture, None, None]:
    handler_id = logger.add(caplog.handler, format="{message}")
    yield caplog
    logger.remove(handler_id)


@pytest.fixture
def base_config() -> ConfigTree:
    config = build_simulation_configuration()
    config.update(
        {
            "time": {
                "start": {
                    "year": 1990,
                },
                "end": {"year": 2010},
                "step_size": 30.5,
            },
            "randomness": {"key_columns": ["entrance_time", "age"]},
        },
        **metadata(__file__, layer="model_override"),
    )
    return config


@pytest.fixture
def test_data_dir() -> Path:
    data_dir = Path(__file__).resolve().parent / "test_data"
    assert data_dir.exists(), "Test directory structure is broken"
    return data_dir


@pytest.fixture(params=[".yaml", ".yml"])
def test_spec(request: pytest.FixtureRequest, test_data_dir: Path) -> Path:
    return test_data_dir / f"mock_model_specification{request.param}"


@pytest.fixture(params=[".yaml", ".yml"])
def test_user_config(request: pytest.FixtureRequest, test_data_dir: Path) -> Path:
    return test_data_dir / f"mock_user_config{request.param}"


@pytest.fixture
def model_specification(
    mocker: pytest_mock.MockFixture, test_spec: Path, test_user_config: Path
) -> ConfigTree:
    expand_user_mock = mocker.patch("vivarium.engine.framework.configuration.Path.expanduser")
    expand_user_mock.return_value = test_user_config
    return build_model_specification(test_spec)


@pytest.fixture
def disease_model_spec(tmp_path: Path) -> Path:
    model_spec_path = (
        Path(__file__).resolve().parent.parent
        / "src/vivarium/engine/examples/disease_model/disease_model.yaml"
    )
    with open(model_spec_path, "r") as file:
        ms = yaml.safe_load(file)

    # modify the time so as not to take so long for a unit test
    ms["configuration"]["time"]["end"]["year"] = ms["configuration"]["time"]["start"]["year"]
    ms["configuration"]["time"]["end"]["month"] = ms["configuration"]["time"]["start"][
        "month"
    ]
    ms["configuration"]["time"]["start"]["day"] = 1
    ms["configuration"]["time"]["end"]["day"] = 5
    ms["configuration"]["time"]["step_size"] = 0.5
    model_spec = tmp_path / "disease_model.yaml"

    with open(model_spec, "w") as file:
        yaml.dump(ms, file)

    return model_spec


@pytest.fixture
def hdf_file_path(tmp_path: Path) -> Path:
    """Path to a freshly-initialized empty artifact."""
    path = tmp_path / "artifact.hdf"
    # The constructor emits "No artifact found at <path>. Building new
    # artifact." since path doesn't exist; silence it - building is the
    # whole point of the fixture.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        Artifact(path)
    return path
