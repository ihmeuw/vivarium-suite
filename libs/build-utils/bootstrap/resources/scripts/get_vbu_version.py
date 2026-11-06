#!/usr/bin/env python3
"""
Script to determine the vivarium_build_utils version for a project.

The intent is that this script will be called by Jenkins via the `get_vbu_version.groovy`
bootstrap script in the /bootstrap/vars/ directory.

This script:
1. For a standalone repo whose uv.lock pins vivarium-build-utils, returns that pin
2. Otherwise reads python_versions.json to get the maximum supported Python version
3. Runs pip with --dry-run to resolve dependencies
4. Extracts the vivarium-build-utils version from the output
5. Returns the monorepo per-lib tag form (``vivarium-build-utils-v<X.Y.Z>``), or a
   commit SHA for a git pin

"""

import json
import os
import re
import subprocess
import sys
from typing import Optional

MINICONDA_DIR = "/svc-simsci/vbu_bootstrap_envs/miniconda3"
IHME_PYPI = "https://artifactory.ihme.washington.edu/artifactory/api/pypi/pypi-shared/"


def _get_package_subdir() -> str:
    """Return ``libs/<pkg>`` for monorepo per-package builds, else empty string.

    Derived from Jenkins' ``JOB_NAME`` ("<prefix>/<repo>/libs/<pkg>/<branch>").
    Standalone repos have no ``libs`` segment, so subdir stays empty and the
    script operates on the workspace root (the pre-monorepo behavior).
    """
    parts = os.environ.get("JOB_NAME", "").split("/")
    try:
        i = parts.index("libs")
    except ValueError:
        return ""
    if i + 1 >= len(parts):
        return ""
    return f"{parts[i]}/{parts[i + 1]}"


def _get_locked_vbu_version(lock_path: str = "uv.lock") -> Optional[str]:
    """Return the vivarium-build-utils pin in ``lock_path``, or None if it has none.

    Parameters
    ----------
    lock_path
        The path to the uv lock file, relative to the working directory.

    Returns
    -------
        ``vivarium-build-utils-v<X.Y.Z>`` for a registry pin, or the full commit SHA
        for a git pin, matching the forms that ``_extract_vbu_version`` returns.
        None when there is no lock, the lock has no vivarium-build-utils entry, or
        the entry has another source (such as a local path) that Jenkins cannot load.

    Notes
    -----
    The lock is scanned line by line rather than parsed with ``tomllib``, because
    the bootstrap ``python3`` may be older than 3.11. A uv lock entry looks like:
        [[package]]
        name = "vivarium-build-utils"
        version = "4.11.2"
        source = { registry = "https://pypi.org/simple" }
    or, for a git source:
        source = { git = "https://github.com/ihmeuw/vivarium-suite.git?subdirectory=libs%2Fbuild-utils&rev=047f6b632#047f6b6322436c129750689618ff365ec21f82cf" }
    """
    if not os.path.exists(lock_path):
        return None
    with open(lock_path, "r") as f:
        lines = [line.strip() for line in f]

    for i, line in enumerate(lines):
        if line != 'name = "vivarium-build-utils"' or lines[i - 1] != "[[package]]":
            continue
        version = None
        source = None
        # The entry's keys run until the next table header.
        for entry_line in lines[i + 1 :]:
            if entry_line.startswith("["):
                break
            if entry_line.startswith("version = "):
                version = entry_line.split('"')[1]
            elif entry_line.startswith("source = "):
                source = entry_line
        if source is not None:
            git_match = re.search(r'git = "[^"]*#([0-9a-f]{40})"', source)
            if git_match:
                return git_match.group(1)
            if "registry = " in source and version is not None:
                return f"vivarium-build-utils-v{version}"
        print(
            f"WARNING: {lock_path} pins vivarium-build-utils with a source Jenkins "
            f"cannot load ({source}); resolving the version instead",
            file=sys.stderr,
        )
        return None
    return None


def _get_max_python_version() -> str:
    """Read python_versions.json and return the maximum supported Python version."""
    # Ensure we're in a directory with python_versions.json
    if not os.path.exists("python_versions.json"):
        print(
            "ERROR: python_versions.json not found in current directory",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        with open("python_versions.json", "r") as f:
            versions: list[str] = json.load(f)
        return max(versions)
    except FileNotFoundError:
        print(
            "ERROR: python_versions.json not found in current directory",
            file=sys.stderr,
        )
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in python_versions.json: {e}", file=sys.stderr)
        sys.exit(1)


def _run_pip_dry_run(python_version: str) -> str:
    """Run pip install --dry-run and return the output.

    Notes
    -----
    This function uses previously-created python-only environments located
    on our shared filesystem, e.g. py311, py312, etc.
    """
    # Format python version for conda environment (e.g., "3.11" -> "311")
    env_version = python_version.replace(".", "")

    # Construct the command to activate conda environment and run pip
    # NOTE: if updating supported python versions, be sure to create a new
    # conda environment with the appropriate python version and uv installed
    # at MINICONDA_DIR/envs/py{env_version}
    #
    # NOTE: We pass `vivarium-build-utils` alongside `.` so the dry-run always resolves
    # vbu, even when the lib's pyproject doesn't declare it. For libs that do declare
    # vbu, the bare requirement adds no constraint and the existing pin drives resolution.
    cmd = f"""
    source {MINICONDA_DIR}/etc/profile.d/conda.sh
    conda activate py{env_version}
    uv pip install --dry-run . vivarium-build-utils --extra-index-url {IHME_PYPI}simple/ --index-strategy unsafe-best-match --no-cache
    """

    output = _run_uv(cmd)

    if "vivarium-build-utils @ file://" in output:
        # When the lib IS vivarium-build-utils, we need to run the dry-run again w/o `.`.
        vbu_cmd = f"""
        source {MINICONDA_DIR}/etc/profile.d/conda.sh
        conda activate py{env_version}
        uv pip install --dry-run vivarium-build-utils --extra-index-url {IHME_PYPI}simple/ --index-strategy unsafe-best-match --no-cache
        """
        output = _run_uv(vbu_cmd)

    return output


def _run_uv(cmd: str) -> str:
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
        # NOTE: uv evidently sends output to stderr
        return result.stderr
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to run pip dry-run: {e}", file=sys.stderr)
        print(f"STDERR: {e.stderr}", file=sys.stderr)
        sys.exit(1)


def _extract_vbu_version(dry_run_output: str) -> str:
    """Extract vivarium-build-utils version from pip dry-run output.

    Returns
    -------
        Either the monorepo-prefixed tag (``vivarium-build-utils-v<X.Y.Z>``)
        when the lib pins a PyPI version, or the bare commit SHA when the lib pins
        a direct git reference. Jenkins's ``library("vivarium_build_utils@<ref>")``
        accepts either form.

    Notes
    -----
    The pip dry-run output includes lines like:
        + vivarium-build-utils==1.2.3
        OR
        + vivarium-build-utils @ git+https://github.com/ihmeuw/vivarium-suite.git@<HASH>
    """
    for line in dry_run_output.split("\n"):
        if "+ vivarium-build-utils" in line:
            # Check for pinned version first
            version_match = re.search(
                r"vivarium-build-utils==([0-9]+\.[0-9]+\.[0-9]+[^\s]*)", line
            )
            if version_match:
                version = version_match.group(1)
                return f"vivarium-build-utils-v{version}"

            # Check for git reference
            git_match = re.search(
                r"vivarium-build-utils @ git\+https://github\.com/ihmeuw/vivarium-suite\.git@([a-f0-9]+)",
                line,
            )
            if git_match:
                commit_hash = git_match.group(1)
                return commit_hash
    print(
        "ERROR: Could not find vivarium_build_utils version in pip dry-run output",
        file=sys.stderr,
    )
    print("Dry-run output:", file=sys.stderr)
    print(dry_run_output, file=sys.stderr)
    sys.exit(1)


def main() -> str:
    """Main function to orchestrate version resolution."""

    subdir = _get_package_subdir()
    if subdir:
        os.chdir(subdir)
        vbu_version = None
    else:
        # A standalone repo with a uv.lock must load the vbu its lock installs, so
        # that the pipeline code and base.mk match the vbu in its environment.
        vbu_version = _get_locked_vbu_version()
    if vbu_version is None:
        python_version = _get_max_python_version()
        dry_run_output = _run_pip_dry_run(python_version)
        vbu_version = _extract_vbu_version(dry_run_output)

    # NOTE: We must print the vbu_version for Jenkins to capture it!
    print(vbu_version)
    return vbu_version


if __name__ == "__main__":
    main()
