import os
import shutil
import tempfile
from pathlib import Path

import pytest


def is_on_slurm() -> bool:
    """Returns True if the current environment is a SLURM cluster."""
    return shutil.which("sbatch") is not None


IS_ON_SLURM = is_on_slurm()
TEST_ARTIFACT_PATH = (
    "/mnt/team/simulation_science/pub/simulation_profiling/artifacts/pakistan.hdf"
)


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
def test_model_specs(tmp_path) -> list[Path]:
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

    return [baseline_file, other_spec_file]


@pytest.fixture
def sample_stats_file(tmp_path) -> Path:
    """Create a minimal cProfile stats.txt file for testing extraction."""
    stats_content = """Mon Jan  6 14:17:08 2026    /tmp/test.stats

         1000 function calls (950 primitive calls) in 10.500 seconds

   Ordered by: cumulative time

   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
        1    0.100    0.100   10.500   10.500 /vivarium/framework/engine.py:248(run_simulation)
        1    0.050    0.050    8.000    8.000 /vivarium/framework/engine.py:300(setup)
        1    0.075    0.075    2.000    2.000 /vivarium/framework/engine.py:350(initialize_simulants)
        1    0.500    0.500    5.000    5.000 /vivarium/framework/engine.py:400(run)
        1    0.025    0.025    0.300    0.300 /vivarium/framework/engine.py:450(finalize)
        1    0.010    0.010    0.100    0.100 /vivarium/framework/engine.py:500(report)
      160    0.100    0.001    2.500    0.016 /vivarium/framework/results/manager.py:129(gather_results)
      280    0.050    0.000    1.200    0.004 /vivarium/framework/values/pipeline.py:66(__call__)
     2459    0.200    0.000    0.800    0.000 /vivarium/framework/population/population_view.py:133(get)
   50/25    0.150    0.003    0.600    0.024 /some/custom/module.py:100(custom_function)
      100    0.025    0.000    0.500    0.005 /another/module.py:200(another_function)
      150    0.030    0.000    0.450    0.003 /vivarium/framework/values/pipeline.py:66(duplicate_func)
      200    0.040    0.000    0.800    0.004 /vivarium/framework/values/pipeline.py:150(duplicate_func)
"""
    stats_file = tmp_path / "test_stats.txt"
    stats_file.write_text(stats_content)
    return stats_file
