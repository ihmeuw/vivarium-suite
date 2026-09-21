"""Assert the per-package ``Makefile`` copies that should match still do.

Most packages ship a byte-identical ``Makefile``, because ``build-env`` has to
run before ``base.mk`` can be included and so cannot be shared from there. That
makes every edit to it a manual propagation across a dozen files, where one
package silently falling behind would not otherwise be noticed.

This checks the files, not the build: nothing here creates an environment or
runs ``make``.
"""

from pathlib import Path

import pytest

# Packages whose Makefile is deliberately not a copy of the standard one.
DIVERGENT_PACKAGES = {
    # This package is vivarium-build-utils, so it bootstraps from the local
    # checkout; installing from PyPI would test a different copy of itself.
    "build-utils",
    # Carries extra build-env arguments (type, lfs, include_timestamp).
    "profiling",
    # Overrides LOCATIONS; ships no Python source for lint to walk.
    "dependencies",
}


def _repo_libs_dir() -> Path:
    """Return the monorepo's ``libs/`` directory, or skip if not in a checkout."""
    for parent in Path(__file__).resolve().parents:
        libs = parent / "libs"
        if (libs / "build-utils" / "pyproject.toml").is_file():
            return libs
    pytest.skip("not running from a vivarium-suite checkout")


@pytest.fixture(scope="module")
def standard_makefiles() -> dict[str, str]:
    """Map package name to Makefile text for every package expected to be a copy."""
    libs = _repo_libs_dir()
    makefiles = {
        path.parent.name: path.read_text()
        for path in sorted(libs.glob("*/Makefile"))
        if path.parent.name not in DIVERGENT_PACKAGES
    }
    assert len(makefiles) > 1, "expected several packages to share one Makefile"
    return makefiles


def test_standard_makefiles_are_identical(standard_makefiles: dict[str, str]) -> None:
    reference_name, reference = sorted(standard_makefiles.items())[0]
    diverged = [name for name, text in standard_makefiles.items() if text != reference]
    assert not diverged, (
        f"these Makefiles no longer match {reference_name}/Makefile: {diverged}. "
        "Propagate the change to every package, or add the package to "
        "DIVERGENT_PACKAGES if the difference is intentional."
    )
