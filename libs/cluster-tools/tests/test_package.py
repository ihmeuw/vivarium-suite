"""Package-level smoke tests."""

import importlib

import pytest

import vivarium.cluster_tools


def test_version_resolves_to_installed_distribution() -> None:
    """Verify ``__version__`` came from importlib.metadata, not the fallback.

    Guards against a misspelled distribution name in ``__init__.py`` silently
    degrading to the ``"0.0.0+not-installed"`` sentinel. Distinct from the
    ``"0.0.0+no-git-tag"`` setuptools_scm fallback so a legitimate shallow
    clone doesn't false-fail this test.
    """
    from packaging.version import Version

    assert vivarium.cluster_tools.__version__ != "0.0.0+not-installed"
    Version(vivarium.cluster_tools.__version__)


@pytest.mark.parametrize(
    "modpath",
    [
        "vivarium.cluster_tools",
        "vivarium.cluster_tools.cli_tools",
        "vivarium.cluster_tools.logs",
        "vivarium.cluster_tools.utilities",
        "vivarium.cluster_tools.psimulate",
        "vivarium.cluster_tools.psimulate.branches",
        "vivarium.cluster_tools.psimulate.environment",
        "vivarium.cluster_tools.psimulate.jobs",
        "vivarium.cluster_tools.psimulate.model_specification",
        "vivarium.cluster_tools.psimulate.paths",
        "vivarium.cluster_tools.psimulate.performance_logger",
        "vivarium.cluster_tools.psimulate.pip_env",
        "vivarium.cluster_tools.psimulate.cluster",
        "vivarium.cluster_tools.psimulate.cluster.cli_options",
        "vivarium.cluster_tools.psimulate.cluster.interface",
        "vivarium.cluster_tools.psimulate.results",
        "vivarium.cluster_tools.psimulate.results.cli_options",
        "vivarium.cluster_tools.psimulate.results.writing",
        "vivarium.cluster_tools.psimulate.worker",
        "vivarium.cluster_tools.psimulate.worker.task_runner",
        "vivarium.cluster_tools.testing.fail_once_component",
        "vivarium.cluster_tools.vipin",
        "vivarium.cluster_tools.vipin.cli",
        "vivarium.cluster_tools.vipin.perf_counters",
        "vivarium.cluster_tools.vipin.perf_report",
    ],
)
def test_submodule_importable(modpath: str) -> None:
    """Each non-jobmon-dependent submodule must import without error.

    Catches stale legacy paths (e.g. ``vivarium_cluster_tools.X`` survivors
    of the rename) and broken namespace setup. jobmon-dependent modules
    (``psimulate.cli``, ``psimulate.runner``, ``psimulate.jobmon_config.*``,
    ``psimulate.worker.vivarium_work_horse``, ``psimulate.worker.load_test_work_horse``)
    are excluded from this list because jobmon is in the ``[cluster]`` extra
    and may not be installed.
    """
    importlib.import_module(modpath)


def test_console_scripts_registered() -> None:
    """Verify each [project.scripts] entry is wired up via entry_points.

    Catches the case where a script is declared in pyproject.toml but the
    installed distribution metadata doesn't reflect it (stale install) or
    where the entry-point target has been renamed/moved.
    """
    from importlib.metadata import entry_points

    scripts = entry_points(group="console_scripts")
    by_name = {ep.name: ep.value for ep in scripts}
    assert by_name.get("psimulate") == "vivarium.cluster_tools.psimulate.cli:psimulate"
    assert by_name.get("vipin") == "vivarium.cluster_tools.vipin.cli:vipin"
