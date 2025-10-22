import os
import tempfile
from pathlib import Path

import pytest


def pytest_addoption(parser):
    parser.addoption("--runslow", action="store_true", default=False, help="run slow tests")


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: mark test as slow to run")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--runslow"):
        # --runslow given in cli: do not skip slow tests
        return
    skip_slow = pytest.mark.skip(reason="need --runslow option to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


@pytest.fixture
def test_model_specs(tmp_path):
    """Create minimal test model specification files in a temporary directory."""

    # Baseline model specification
    baseline_spec = """
components:
    vivarium:
        examples:
            disease_model:
                - BasePopulation()

configuration:
    time:
        start:
            year: 2022
            month: 1
            day: 1
        end:
            year: 2022
            month: 1
            day: 2  # Single timestep (1 days)
        step_size: 1 # Days
    population:
        population_size: 10  # Small population for fast testing
"""
    other_spec = baseline_spec.replace("year: 2022", "year: 2023")

    # Create spec files
    baseline_file = tmp_path / "model_spec_baseline.yaml"
    other_spec_file = tmp_path / "model_spec_other.yaml"

    baseline_file.write_text(baseline_spec)
    other_spec_file.write_text(other_spec)

    return [str(baseline_file), str(other_spec_file)]
