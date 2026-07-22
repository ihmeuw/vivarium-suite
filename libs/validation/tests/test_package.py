"""Smoke tests for the ``vivarium-validation`` distribution."""
import inspect
from importlib.metadata import version

import pytest
from packaging.version import Version


def test_distribution_is_installed() -> None:
    """Verify the distribution metadata resolves to a real version.

    Reads distribution metadata without importing the package, so it runs even
    without the ``validation`` extra (whose IHME-artifactory-only deps the
    package imports at module load). Guards against a misspelled distribution
    name and ensures ``make test-all`` always collects at least one test.
    """
    Version(version("vivarium-validation"))


def test_version_resolves_to_installed_distribution() -> None:
    """Verify ``__version__`` came from importlib.metadata, not the fallback.

    Guards against a misspelled distribution name in ``__init__.py`` silently
    degrading to the ``"0.0.0+not-installed"`` sentinel. Requires the
    ``validation`` extra, since importing the package pulls ``vivarium-inputs``.
    """
    pytest.importorskip("vivarium_inputs")
    pytest.importorskip("vivarium.artifact")

    import vivarium.validation

    assert vivarium.validation.__version__ != "0.0.0+not-installed"
    Version(vivarium.validation.__version__)


def test_public_api_exports() -> None:
    """Verify the package exposes ``ValidationContext`` as the real class.

    Guards against ``__init__`` drift dropping the export (which a plain import
    of the package would not catch). Requires the ``validation`` extra, since
    importing the package pulls ``vivarium-inputs``.
    """
    pytest.importorskip("vivarium_inputs")
    pytest.importorskip("vivarium.artifact")

    import vivarium.validation

    assert inspect.isclass(vivarium.validation.ValidationContext)
