"""Shared fixtures for the psimulate test suite."""

from pathlib import Path
from typing import Any, Generator

import pytest
from _pytest.logging import LogCaptureFixture
from loguru import logger

from vivarium_cluster_tools.psimulate.jobs import JobParameters


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
        "backup_configuration": {},
        "extras": {},
    }
    defaults.update(overrides)
    return JobParameters(**defaults)


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
