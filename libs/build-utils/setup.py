"""Custom build step.

Project metadata lives in pyproject.toml (PEP 621). This file exists only to
ship resources/ inside the wheel. resources/ sits at the lib root so Jenkins
can also load it as a shared-library resource; CustomBuildPy mirrors it into
the package directory at build time so the wheel exposes
vivarium/build_utils/resources/ for get_resources_path() / get_makefiles_path().
"""
import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


class CustomBuildPy(build_py):
    def run(self) -> None:
        super().run()
        repo_root = Path(__file__).parent
        resources_src = repo_root / "resources"
        if not resources_src.exists():
            return
        build_lib = Path(self.build_lib)
        resources_dest = build_lib / "vivarium" / "build_utils" / "resources"
        if resources_dest.exists():
            shutil.rmtree(resources_dest)
        shutil.copytree(resources_src, resources_dest)


setup(cmdclass={"build_py": CustomBuildPy})
