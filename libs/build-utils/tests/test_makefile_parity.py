"""Guard against drift between the per-package ``Makefile`` copies.

``build-env`` cannot live in the shared ``base.mk`` - it has to bootstrap
``vivarium.build_utils`` into a fresh conda env before ``base.mk`` can be
included - so every package carries its own copy of that target. The copies are
maintained by hand-propagating the same edit, which is exactly the situation
where one package silently falls behind.
"""

import re
from pathlib import Path

import pytest

# Packages whose Makefile is deliberately not a copy of the standard one.
DIVERGENT_PACKAGES = {
    # Bootstraps vbu from the local checkout rather than PyPI (it is vbu).
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


def test_divergent_packages_still_define_build_env() -> None:
    """Check the exempt packages kept ``build-env`` and its private helper."""
    libs = _repo_libs_dir()
    for package in sorted(DIVERGENT_PACKAGES):
        text = (libs / package / "Makefile").read_text()
        # Anchored to a line start: an unanchored "build-env:" also matches the
        # "_build-env:" helper, so a package could drop the public target undetected.
        targets = re.findall(r"^(_?build-env):", text, flags=re.MULTILINE)
        assert "build-env" in targets, f"{package} lost its build-env target"
        assert "_build-env" in targets, f"{package} lost its _build-env helper"
