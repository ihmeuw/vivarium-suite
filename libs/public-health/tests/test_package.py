"""Package-level smoke tests."""

import importlib

import pytest

import vivarium.public_health


def test_version_resolves_to_installed_distribution() -> None:
    """Verify ``__version__`` came from importlib.metadata, not the fallback.

    Guards against a misspelled distribution name in ``__init__.py`` silently
    degrading to the ``"0.0.0+not-installed"`` sentinel.
    """
    from packaging.version import Version

    assert vivarium.public_health.__version__ != "0.0.0+not-installed"
    Version(vivarium.public_health.__version__)


@pytest.mark.parametrize(
    "modpath",
    [
        "vivarium.public_health",
        "vivarium.public_health._example_data",
        "vivarium.public_health.causal_factor",
        "vivarium.public_health.causal_factor.calibration_constant",
        "vivarium.public_health.causal_factor.distributions",
        "vivarium.public_health.causal_factor.effect",
        "vivarium.public_health.causal_factor.exposure",
        "vivarium.public_health.causal_factor.utilities",
        "vivarium.public_health.disease",
        "vivarium.public_health.disease.exceptions",
        "vivarium.public_health.disease.model",
        "vivarium.public_health.disease.models",
        "vivarium.public_health.disease.special_disease",
        "vivarium.public_health.disease.state",
        "vivarium.public_health.disease.transition",
        "vivarium.public_health.plugins",
        "vivarium.public_health.plugins.parser",
        "vivarium.public_health.population",
        "vivarium.public_health.population.add_new_birth_cohorts",
        "vivarium.public_health.population.base_population",
        "vivarium.public_health.population.data_transformations",
        "vivarium.public_health.population.mortality",
        "vivarium.public_health.results",
        "vivarium.public_health.results.causal_factor",
        "vivarium.public_health.results.columns",
        "vivarium.public_health.results.disability",
        "vivarium.public_health.results.disease",
        "vivarium.public_health.risks",
        "vivarium.public_health.risks.base_risk",
        "vivarium.public_health.risks.effect",
        "vivarium.public_health.treatment",
        "vivarium.public_health.utilities",
    ],
)
def test_submodule_importable(modpath: str) -> None:
    """Each submodule must import without error.

    Catches stale legacy paths (e.g. ``vivarium_public_health.X`` survivors of
    the rename), broken namespace setup, and missing transitive deps.
    """
    importlib.import_module(modpath)


def test_top_level_reexports_resolve() -> None:
    """Top-level re-exports must resolve to canonical source symbols.

    The `vivarium.public_health.__init__` re-exports a curated set of names
    from subpackages. Verify the wiring is intact end-to-end.
    """
    from vivarium.public_health.disease import DiseaseModel as DiseaseModel_src
    from vivarium.public_health.plugins import (
        CausesConfigurationParser as CausesConfigurationParser_src,
    )
    from vivarium.public_health.results import DiseaseObserver as DiseaseObserver_src
    from vivarium.public_health.risks import Risk as Risk_src

    assert vivarium.public_health.DiseaseModel is DiseaseModel_src
    assert vivarium.public_health.CausesConfigurationParser is CausesConfigurationParser_src
    assert vivarium.public_health.DiseaseObserver is DiseaseObserver_src
    assert vivarium.public_health.Risk is Risk_src


def test_namespace_coexistence_with_engine_owner() -> None:
    """vivarium.public_health must coexist with the canonical vivarium namespace.

    vivarium-engine owns `vivarium/__init__.py`; this lib registers a
    subpackage under that namespace. Importing both in the same interpreter
    must not collide.
    """
    import vivarium.engine  # noqa: F401

    assert vivarium.public_health.__name__ == "vivarium.public_health"
