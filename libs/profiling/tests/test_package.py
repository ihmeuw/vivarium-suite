"""Package-level smoke tests."""

import vivarium.profiling


def test_version_resolves_to_installed_distribution() -> None:
    """Verify ``__version__`` came from importlib.metadata, not the fallback.

    Guards against a misspelled distribution name in ``__init__.py`` silently
    degrading to the ``"0.0.0+not-installed"`` sentinel. Distinct from the
    ``"0.0.0+no-git-tag"`` setuptools_scm fallback so a legitimate shallow
    clone doesn't false-fail this test.
    """
    from packaging.version import Version

    assert vivarium.profiling.__version__ != "0.0.0+not-installed"
    Version(vivarium.profiling.__version__)


def test_console_scripts_registered() -> None:
    """Verify each [project.scripts] entry is wired up via entry_points.

    Catches the case where a script is declared in pyproject.toml but the
    installed distribution metadata doesn't reflect it (stale install) or
    where the entry-point target has been renamed/moved.
    """
    from importlib.metadata import entry_points

    expected = (
        "make_artifacts",
        "run_benchmark",
        "profile_sim",
        "summarize",
    )
    scripts = entry_points(group="console_scripts")
    missing = [name for name in expected if name not in scripts.names]
    assert not missing, f"missing console scripts: {missing}"
