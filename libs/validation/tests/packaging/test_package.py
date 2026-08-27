"""Smoke tests for the ``vivarium-validation`` distribution."""
import inspect
import subprocess
import sys
import textwrap
from importlib.metadata import version

from packaging.version import Version


def test_distribution_is_installed() -> None:
    """Verify the distribution metadata resolves to a real version.

    Guards against a misspelled distribution name and ensures ``make test-all``
    always collects at least one test.
    """
    Version(version("vivarium-validation"))


def test_version_resolves_to_installed_distribution() -> None:
    """Verify ``__version__`` came from importlib.metadata, not the fallback.

    Guards against a misspelled distribution name in ``__init__.py`` silently
    degrading to the ``"0.0.0+not-installed"`` sentinel.
    """
    import vivarium.validation

    assert vivarium.validation.__version__ != "0.0.0+not-installed"
    Version(vivarium.validation.__version__)


def test_public_api_exports() -> None:
    """Verify the package exposes ``ValidationContext`` as the real class.

    Guards against ``__init__`` drift dropping the export (which a plain import
    of the package would not catch).
    """
    import vivarium.validation

    assert inspect.isclass(vivarium.validation.ValidationContext)


def test_imports_without_artifactory_only_dependencies() -> None:
    """Verify the package imports without ``vivarium-inputs`` installed.

    ``vivarium-inputs`` is IHME-artifactory-only, so it is declared in the
    ``gbd`` extra and imported lazily by the modules that need it. A
    module-load-time import of it would make ``pip install vivarium-validation``
    yield a package that cannot be imported at all. Runs in a subprocess with
    the module blocked so the check holds on Jenkins too, where the extra is
    installed.
    """
    script = textwrap.dedent(
        """
        import sys

        class BlockVivariumInputs:
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0] == "vivarium_inputs":
                    raise ModuleNotFoundError(name=fullname)
                return None

        sys.meta_path.insert(0, BlockVivariumInputs())
        import vivarium.validation
        """
    )
    subprocess.run([sys.executable, "-c", script], check=True)
