"""
=====================================
Reproducible Programming Environments
=====================================

Tools to manage and validate reproducible Python environments for simulation runs.

"""
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

from loguru import logger


def validate(output_dir: Path):
    original_environment_file = output_dir / "requirements.txt"

    pip_list_proc = subprocess.Popen(
        ["pip", "list", "--format=freeze"], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    out, err = pip_list_proc.communicate()
    current_environment_list = out.decode().strip().split("\n")

    if not original_environment_file.exists():  # original run
        logger.info(f'Writing environment file to {str(original_environment_file)}.')
        with original_environment_file.open("w") as f:
            f.write("\n".join(current_environment_list))
    else:  # compare with original
        with original_environment_file.open("r") as f:
            original_environment_list = [line.replace("\n", "") for line in f]
        current_environment = _convert_pip_list_to_dict(current_environment_list)
        original_environment = _convert_pip_list_to_dict(original_environment_list)
        _compare_environments(current_environment, original_environment)
        logger.info(
            "Validation of environment successful. All pip installed packages match "
            "original versions. Run can proceed."
        )


def _parse_package_version(s: str) -> Tuple[str, str]:
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


def _convert_pip_list_to_dict(pf_list: List[str]) -> Dict:
    return {p: v for p, v in [_parse_package_version(s) for s in pf_list]}


def _compare_environments(current: Dict, original: Dict):
    differences = []

    current_packages = set(current.keys())
    original_packages = set(original.keys())

    new_packages = current_packages.difference(original_packages)
    if new_packages:
        differences.append(
            f"The current environment contains the following packages not present in the original "
            f"environment: {new_packages}."
        )

    missing_packages = original_packages.difference(current_packages)
    if missing_packages:
        differences.append(
            f"The current environment is missing the following packages present in the original "
            f"environment: {missing_packages}."
        )

    differing_versions = []
    for p in current_packages.intersection(original_packages):
        if current[p] != original[p]:
            differing_versions.append(f"{p}: {original[p]} -> {current[p]}")

    if differing_versions:
        differences.append(
            f"Different versions found in current and original environment for the following "
            f"packages: {differing_versions}."
        )

    if differences:
        differences = "\n".join(differences)
        raise ValueError(
            f"Differences found between environment used for original run and current environment. "
            f"In order to successfully run, you should make a new environment using the requirements.txt "
            f"file found in the output directory. Differences found as follows: {differences}."
        )


