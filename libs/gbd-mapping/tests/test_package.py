"""Smoke tests for the ``vivarium-gbd-mapping`` distribution."""

import vivarium.gbd_mapping


def test_version_resolves_to_installed_distribution():
    """Verify ``__version__`` came from importlib.metadata, not the fallback.

    Guards against a misspelled distribution name in ``__init__.py`` silently
    degrading to the ``"0.0.0+not-installed"`` sentinel. Distinct from the
    ``"0.0.0+no-git-tag"`` setuptools_scm fallback so a legitimate shallow
    clone doesn't false-fail this test.
    """
    from packaging.version import Version

    assert vivarium.gbd_mapping.__version__ != "0.0.0+not-installed"
    Version(vivarium.gbd_mapping.__version__)


def test_public_api_reexports():
    """Verify each documented re-export is reachable on the package.

    A regression that removes a name from ``__init__.py`` raises
    ``AttributeError`` here with the specific missing name in the message.
    """
    expected = (
        "Categories",
        "Cause",
        "Covariate",
        "Etiology",
        "GbdRecord",
        "Healthstate",
        "ModelableEntity",
        "Restrictions",
        "RiskFactor",
        "Sequela",
        "Tmred",
        "UNKNOWN",
        "UnknownEntityError",
        "c_id",
        "causes",
        "cov_id",
        "covariates",
        "etiologies",
        "hs_id",
        "me_id",
        "rei_id",
        "risk_factors",
        "s_id",
        "scalar",
        "sequelae",
    )
    for name in expected:
        getattr(vivarium.gbd_mapping, name)


def test_generator_subpackage_importable():
    """Verify the build_mapping entry-point target resolves.

    Catches typos in the [project.scripts] entry point declaration in
    pyproject.toml or rename drift in the generator package.
    """
    from vivarium.gbd_mapping_generator.build_mapping import build_mapping

    assert callable(build_mapping)


def test_build_mapping_console_script_registered():
    """Verify the build_mapping console script is wired up via entry_points.

    Catches the case where the entry point is declared in pyproject.toml but
    the installed distribution metadata doesn't reflect it (stale install).
    """
    from importlib.metadata import entry_points

    scripts = entry_points(group="console_scripts")
    assert "build_mapping" in scripts.names
