"""Smoke tests for the ``vivarium-workflow`` distribution."""
import inspect

from packaging.version import Version

import vivarium.workflow
from vivarium.workflow import backend, frontend


def test_version_resolves_to_installed_distribution() -> None:
    """Verify ``__version__`` came from importlib.metadata, not the fallback.

    Guards against a misspelled distribution name in ``__init__.py`` silently
    degrading to the ``"0.0.0+not-installed"`` sentinel.
    """
    assert vivarium.workflow.__version__ != "0.0.0+not-installed"
    Version(vivarium.workflow.__version__)


def test_public_api_exports() -> None:
    """Verify the subpackages expose their public API as the real objects."""
    assert inspect.isclass(backend.Task)
    assert inspect.isfunction(backend.check_unique_task_names)
    assert inspect.isfunction(frontend.python_step)
