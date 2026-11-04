"""Smoke tests for the ``vivarium-dagger`` distribution."""
import inspect

from packaging.version import Version

import vivarium.dagger
from vivarium.dagger import backend, frontend


def test_version_resolves_to_installed_distribution() -> None:
    """Verify ``__version__`` came from importlib.metadata, not the fallback.

    Guards against a misspelled distribution name in ``__init__.py`` silently
    degrading to the ``"0.0.0+not-installed"`` sentinel.
    """
    assert vivarium.dagger.__version__ != "0.0.0+not-installed"
    Version(vivarium.dagger.__version__)


def test_public_api_exports() -> None:
    """Verify the subpackages expose their public API as the real objects."""
    assert inspect.isclass(backend.Task)
    assert inspect.isfunction(backend.check_unique_task_names)
    assert inspect.isfunction(frontend.python_step)
