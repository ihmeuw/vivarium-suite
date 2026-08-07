"""Tests for the naming variables in ``resources/makefiles/base.mk``.

Each repo's own Makefile sets ``PACKAGE_NAME`` to ``$(notdir $(CURDIR))``, so it is
a checkout-directory name rather than a package identity - under Jenkins it is the
job-derived workspace name. ``base.mk`` therefore derives ``PACKAGE_DIR`` itself
rather than reading whatever a repo assigned.
"""

import subprocess
from pathlib import Path

from vivarium.build_utils.resources import get_makefiles_path

BASE_MK = Path(get_makefiles_path()) / "base.mk"

# A realistic Jenkins workspace directory name, used as a PACKAGE_NAME override to
# prove base.mk ignores it.
JENKINS_WORKSPACE_NAME = "Private_engine_main@2"


def _debug(cwd: Path, *overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "-f", str(BASE_MK), "debug", *overrides],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _debug_value(result: subprocess.CompletedProcess[str], key: str) -> str:
    values = [
        line.split(":", 1)[1].strip()
        for line in result.stdout.splitlines()
        if line.startswith(f"{key}:")
    ]
    assert values, result.stdout
    return values[0]


def test_conda_env_name_defaults_to_directory(tmp_path: Path) -> None:
    package_dir = tmp_path / "engine"
    package_dir.mkdir()

    result = _debug(
        package_dir, f"PACKAGE_NAME={JENKINS_WORKSPACE_NAME}", "PYTHON_VERSION=3.11"
    )

    assert result.returncode == 0, result.stderr
    assert _debug_value(result, "PACKAGE_DIR") == "engine"
    assert _debug_value(result, "CONDA_ENV_NAME") == "engine_py3.11"


def test_monorepo_guard_keys_off_directory(tmp_path: Path) -> None:
    # A [project] block whose name can't be parsed must fail the build even when
    # PACKAGE_NAME is unrelated to the directory - the guard is about where make is
    # running, which only PACKAGE_DIR reports faithfully.
    package_dir = tmp_path / "libs" / "engine"
    package_dir.mkdir(parents=True)
    (package_dir / "pyproject.toml").write_text('[project]\ndescription = "no name key"\n')

    result = _debug(package_dir, f"PACKAGE_NAME={JENKINS_WORKSPACE_NAME}")

    assert result.returncode != 0
    assert "DIST_NAME_FROM_PROJECT parse failed" in result.stdout + result.stderr
