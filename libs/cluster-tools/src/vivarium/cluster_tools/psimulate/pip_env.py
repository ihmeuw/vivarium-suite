"""
=====================================
Reproducible Programming Environments
=====================================

Tools to manage and validate reproducible Python environments for simulation runs.

"""

import importlib.metadata
import os
import re
from pathlib import Path

import click
from loguru import logger


def validate(environment_file: Path) -> None:
    """Write the installed packages to ``environment_file``, or compare them with it.

    Packages are read from the running interpreter, not with ``pip``, so this
    works in venvs that have no ``pip``, such as those uv builds.
    """
    current_environment_list = _list_installed_packages()

    # Update permissions mask (assign to variable to avoid printing previous value)
    _ = os.umask(0o002)

    if not environment_file.exists():  # original run
        logger.debug(f"Writing environment file to {str(environment_file)}.")
        environment_file.write_text("\n".join(current_environment_list))
    else:  # compare with original
        with environment_file.open() as f:
            original_environment_list = [line.replace("\n", "") for line in f]
        current_environment = _convert_pip_list_to_dict(current_environment_list)
        original_environment = _convert_pip_list_to_dict(original_environment_list)
        _compare_environments(current_environment, original_environment)
        logger.debug(
            "Validation of environment successful. All pip installed packages match "
            "original versions. Run can proceed."
        )


def _list_installed_packages() -> list[str]:
    """Return ``<name>==<version>`` for each installed package, in ``pip list`` order."""
    packages: dict[str, str] = {}
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"]
        # Keep the first copy of a package, which is the one Python imports.
        if name and _normalize_name(name) not in packages:
            packages[_normalize_name(name)] = f"{name}=={dist.version}"
    return [packages[key] for key in sorted(packages)]


def _normalize_name(name: str) -> str:
    """Return the PEP 503 normalized form of a package name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_package_version(s: str) -> tuple[str, str]:
    if (
        "no version control" in s
    ):  # installed from non-git controlled source code in editable mode
        s = s.split("(")[1].split(")")[0]  # yields <package>==<version>

    packaged = s.split("==")
    if len(packaged) == 2:  # not installed from source code, e.g., <package>==<version>
        package = packaged[0]
        version = packaged[1]
    else:  # installed from source code in editable mode so more parsing required
        sourced = s.split("=")
        package = sourced[-1]
        version = sourced[0].split("@")[-1].replace("#egg", "")  # version here is git hash
    return package, version


def _convert_pip_list_to_dict(pf_list: list[str]) -> dict[str, str]:
    # Normalize names so a file that pip wrote compares cleanly with this one.
    return {_normalize_name(p): v for p, v in [_parse_package_version(s) for s in pf_list]}


def _compare_environments(current: dict[str, str], original: dict[str, str]) -> None:
    differences: list[str] = []

    current_packages = set(current.keys())
    original_packages = set(original.keys())

    new_packages = current_packages.difference(original_packages)
    if new_packages:
        differences.append(
            f"The current environment contains the following packages not present "
            f"in the original environment: {new_packages}."
        )

    missing_packages = original_packages.difference(current_packages)
    if missing_packages:
        differences.append(
            f"The current environment is missing the following packages present in the original "
            f"environment: {missing_packages}."
        )

    differing_versions: list[str] = []
    for p in current_packages.intersection(original_packages):
        if current[p] != original[p]:
            differing_versions.append(f"{p}: {original[p]} -> {current[p]}")

    if differing_versions:
        differences.append(
            f"Different versions found in current and original environment for the following "
            f"packages: {differing_versions}."
        )

    if differences:
        differences_str = "\n".join(differences)
        logger.info(
            "Differences found between current environment and original environment used for "
            f" this run. \n\nDifferences found are as follows: {differences_str}. Would you like to proceed?"
        )
        click.confirm(
            "Would you like to proceed psimulate restart with the new environment?",
            abort=True,
        )
