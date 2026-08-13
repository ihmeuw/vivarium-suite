"""Shared fixtures for the psimulate test suite."""

import io
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from _pytest.logging import LogCaptureFixture
from loguru import logger

from vivarium.cluster_tools.psimulate.jobs import BackupConfiguration, JobParameters


def make_job_parameters(**overrides: Any) -> JobParameters:
    """Create a ``JobParameters`` with sensible test defaults.

    Any keyword argument matching a ``JobParameters`` field will override
    the default value.  This keeps individual tests concise while making
    the shared boilerplate explicit in one place.
    """
    defaults: dict[str, Any] = {
        "model_specification": "test_model_spec.yaml",
        "branch_configuration": {},
        "input_draw": 0,
        "random_seed": 0,
        "results_path": "~/tmp",
        "worker_logging_root": "/tmp/worker_logs",
        "backup_configuration": BackupConfiguration(
            backup_dir="/tmp/backups",
            backup_freq=None,
            backup_metadata_path="/tmp/backup_metadata.csv",
        ),
        "extras": {},
    }
    defaults.update(overrides)
    return JobParameters(**defaults)


@pytest.fixture()
def captured_logs() -> Generator[io.StringIO, None, None]:
    """Capture every loguru record emitted during the test.

    An owned sink rather than the ambient ones, so what is asserted on does not
    depend on how the process happens to have configured logging.
    """
    buffer = io.StringIO()
    handler_id = logger.add(buffer, level="TRACE")
    yield buffer
    logger.remove(handler_id)


@pytest.fixture()
def results_dir(tmp_path: Path) -> Path:
    """A temporary ``results`` directory."""
    d = tmp_path / "results"
    d.mkdir()
    return d


@pytest.fixture()
def metadata_dir(tmp_path: Path) -> Path:
    """A temporary ``metadata`` directory."""
    d = tmp_path / "metadata"
    d.mkdir()
    return d
