"""Smoke tests for the ``vivarium-dependencies`` code-less metapackage.

vivarium-dependencies ships no Python modules - it is purely a
``pyproject.toml`` extras manifest. These tests exist to:

1. Satisfy ``make test-all`` (and therefore the root release workflow's
   test step) on a lib that would otherwise have no ``tests/`` dir.
2. Lock in the no-source contract: if someone accidentally adds a
   ``src/vivarium_dependencies/`` (or similar) module, the second test
   below will catch it before it changes the package's nature.
"""
from importlib.metadata import metadata
from importlib.util import find_spec

from packaging.version import Version


def test_version_resolves_to_installed_distribution() -> None:
    """Verify the distribution is installed with a well-formed version."""
    Version(metadata("vivarium-dependencies")["Version"])


def test_metapackage_ships_no_python_modules() -> None:
    """Lock in that the metapackage contains no importable Python source.

    Adding a module under either name would silently change the package's
    contract from "extras manifest" to "package with code"; this test
    catches that regression. ``find_spec`` returns ``None`` for a missing
    top-level module, but raises ``ModuleNotFoundError`` when it can't resolve
    the parent of a dotted name — both outcomes mean "not importable here".
    """
    for name in ("vivarium_dependencies", "vivarium.dependencies"):
        try:
            spec = find_spec(name)
        except ModuleNotFoundError:
            spec = None
        assert spec is None, f"{name!r} unexpectedly resolved to {spec!r}"
