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
    vivarium_public_health:
        population:
            - BasePopulation()

configuration:
    input_data:
        input_draw_number: 0
        artifact_path: '/mnt/team/simulation_science/pub/simulation_profiling/artifacts/pakistan.hdf'
    interpolation:
        order: 0
        extrapolate: True
    randomness:
        map_size: 1_000_000
        key_columns: ['entrance_time', 'age']
        random_seed: 0
    time:
        start:
            year: 2022
            month: 1
            day: 1
        end:
            year: 2022
            month: 1
            day: 29  # Single timestep (28 days)
        step_size: 28 # Days
    population:
        population_size: 1_000  # Small population for fast testing
        initialization_age_min: 0
        initialization_age_max: 100
        untracking_age: 110

    stratification:
        default:
            - 'age_group'
            - 'sex'
"""

    # First non-baseline model with disease component
    disease_spec = """
components:
    vivarium_public_health:
        population:
            - BasePopulation()
        disease:
            - SIS_fixed_duration("lower_respiratory_infections", "28")

configuration:
    input_data:
        input_draw_number: 0
        artifact_path: '/mnt/team/simulation_science/pub/simulation_profiling/artifacts/pakistan.hdf'
    interpolation:
        order: 0
        extrapolate: True
    randomness:
        map_size: 1_000_000
        key_columns: ['entrance_time', 'age']
        random_seed: 0
    time:
        start:
            year: 2022
            month: 1
            day: 1
        end:
            year: 2022
            month: 1
            day: 29  # Single timestep (28 days)
        step_size: 28 # Days
    population:
        population_size: 1_000  # Small population for fast testing
        initialization_age_min: 0
        initialization_age_max: 100
        untracking_age: 110

    stratification:
        default:
            - 'age_group'
            - 'sex'
"""

    # Second non-baseline model with risk component
    risk_spec = """
components:
    vivarium_public_health:
        population:
            - BasePopulation()
        risks:
            - Risk("risk_factor.high_systolic_blood_pressure")

configuration:
    input_data:
        input_draw_number: 0
        artifact_path: '/mnt/team/simulation_science/pub/simulation_profiling/artifacts/pakistan.hdf'
    interpolation:
        order: 0
        extrapolate: True
    randomness:
        map_size: 1_000_000
        key_columns: ['entrance_time', 'age']
        random_seed: 0
    time:
        start:
            year: 2022
            month: 1
            day: 1
        end:
            year: 2022
            month: 1
            day: 29  # Single timestep (28 days)
        step_size: 28 # Days
    population:
        population_size: 1_000  # Small population for fast testing
        initialization_age_min: 0
        initialization_age_max: 100
        untracking_age: 110

    stratification:
        default:
            - 'age_group'
            - 'sex'
"""

    # Create spec files
    baseline_file = tmp_path / "model_spec_baseline.yaml"
    disease_file = tmp_path / "model_spec_disease.yaml"
    risk_file = tmp_path / "model_spec_risk.yaml"

    baseline_file.write_text(baseline_spec)
    disease_file.write_text(disease_spec)
    risk_file.write_text(risk_spec)

    return {
        "baseline": str(baseline_file),
        "disease": str(disease_file),
        "risk": str(risk_file),
        "all": [str(baseline_file), str(disease_file), str(risk_file)],
    }
