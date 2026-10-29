"""Test that the code staying in cluster-tools imports without the code moving out."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import vivarium.cluster_tools

_PACKAGE_DIR = Path(vivarium.cluster_tools.__file__).parent
# These two modules exist to call the Jobmon SDK, so they import it at module level.
_JOBMON_MODULES = {
    "vivarium.cluster_tools.core.jobmon.artifact",
    "vivarium.cluster_tools.core.jobmon.client",
}
_MOVING_OUT = (
    "vivarium.engine",
    "vivarium.cluster_tools.psimulate",
    "vivarium.cluster_tools.dagger",
    "vivarium.cluster_tools.vipin",
)


def _staying_modules() -> list[str]:
    """Return the package, ``utilities`` and every ``core`` module by dotted name."""
    names = ["vivarium.cluster_tools", "vivarium.cluster_tools.utilities"]
    for path in sorted((_PACKAGE_DIR / "core").rglob("*.py")):
        parts = path.relative_to(_PACKAGE_DIR).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.append(".".join(("vivarium.cluster_tools", *parts)))
    return names


def _loaded_after_importing(
    modules: list[str], packages: tuple[str, ...], cwd: Path
) -> list[str]:
    """Import ``modules`` in a fresh interpreter; return the loaded modules of ``packages``."""
    script = (
        "import importlib, json, sys\n"
        f"for name in {modules!r}:\n"
        "    importlib.import_module(name)\n"
        f"packages = {packages!r}\n"
        "loaded = [m for m in sys.modules if any(m == p or m.startswith(p + '.') for p in packages)]\n"
        "print(json.dumps(sorted(loaded)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, cwd=cwd
    )
    assert result.returncode == 0, result.stderr
    loaded: list[str] = json.loads(result.stdout.splitlines()[-1])
    return loaded


def test_staying_modules_are_found() -> None:
    modules = _staying_modules()
    assert _JOBMON_MODULES < set(modules)
    assert "vivarium.cluster_tools.core.cluster.validation" in modules
    assert "vivarium.cluster_tools.core.jobmon.env" in modules


def test_staying_code_imports_without_jobmon_or_the_moving_code(tmp_path: Path) -> None:
    modules = [name for name in _staying_modules() if name not in _JOBMON_MODULES]
    assert _loaded_after_importing(modules, (*_MOVING_OUT, "jobmon"), tmp_path) == []


def test_jobmon_modules_import_without_the_moving_code(tmp_path: Path) -> None:
    pytest.importorskip("jobmon")
    assert _loaded_after_importing(_staying_modules(), _MOVING_OUT, tmp_path) == []
