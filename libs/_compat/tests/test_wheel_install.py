import subprocess
import sys
import venv as venv_module
from pathlib import Path

import pytest

_PACKAGE_DIR = Path(__file__).parent.parent


def test_pth_in_site_packages_after_wheel_install(tmp_path):
    """Non-editable wheel install must put vivarium_compat.pth in site-packages."""
    venv_dir = tmp_path / "venv"
    venv_module.create(str(venv_dir), with_pip=True, clear=True)

    pip = venv_dir / "bin" / "pip"
    python = venv_dir / "bin" / "python"

    subprocess.run(
        [str(pip), "install", str(_PACKAGE_DIR), "--no-cache-dir", "-q"],
        check=True,
    )

    # .pth must be in site-packages, not sys.prefix
    result = subprocess.run(
        [
            str(python),
            "-c",
            "import site, pathlib; "
            "sp = pathlib.Path(site.getsitepackages()[0]); "
            "print((sp / 'vivarium_compat.pth').exists())",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "True", (
        "vivarium_compat.pth not found in site-packages — "
        "check [tool.hatch.build.targets.wheel.force-include] in pyproject.toml"
    )


def test_hook_active_at_startup_after_wheel_install(tmp_path):
    """After wheel install, _CompatFinder must be in sys.meta_path before any user code."""
    venv_dir = tmp_path / "venv"
    venv_module.create(str(venv_dir), with_pip=True, clear=True)

    pip = venv_dir / "bin" / "pip"
    python = venv_dir / "bin" / "python"

    subprocess.run(
        [str(pip), "install", str(_PACKAGE_DIR), "--no-cache-dir", "-q"],
        check=True,
    )

    result = subprocess.run(
        [
            str(python),
            "-c",
            "import sys; "
            "from vivarium._compat._compat import _CompatFinder; "
            "print(any(isinstance(f, _CompatFinder) for f in sys.meta_path))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert (
        result.stdout.strip() == "True"
    ), "_CompatFinder not in sys.meta_path at startup — .pth file may not be firing"
