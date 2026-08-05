"""Tests for the naming variables in ``resources/makefiles/base.mk``.

``PACKAGE_NAME`` comes from each repo's own Makefile as ``$(notdir $(CURDIR))``,
so it is a checkout-directory name rather than a package identity. ``base.mk``
therefore derives ``PACKAGE_DIR`` itself for directory checks instead of trusting
whatever a repo assigned.
"""

import subprocess
from pathlib import Path

from vivarium.build_utils.resources import get_makefiles_path

BASE_MK = Path(get_makefiles_path()) / "base.mk"


def test_monorepo_guard_keys_off_directory_not_package_name(tmp_path: Path) -> None:
    # A [project] block whose name can't be parsed must fail the build even when
    # PACKAGE_NAME has been set to something unrelated to the directory - the guard
    # is about where make is running, which only PACKAGE_DIR reports faithfully.
    package_dir = tmp_path / "libs" / "engine"
    package_dir.mkdir(parents=True)
    (package_dir / "pyproject.toml").write_text('[project]\ndescription = "no name key"\n')

    result = subprocess.run(
        ["make", "-f", str(BASE_MK), "debug", "PACKAGE_NAME=Private_engine_main@2"],
        cwd=package_dir,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "DIST_NAME_FROM_PROJECT parse failed" in result.stdout + result.stderr
