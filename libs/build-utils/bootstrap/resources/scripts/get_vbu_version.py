#!/usr/bin/env python3
"""
Script to determine the vivarium_build_utils version for a project.

The intent is that this script will be called by Jenkins via the `get_vbu_version.groovy`
bootstrap script in the /bootstrap/vars/ directory.

This script:
1. Reads python_versions.json to get the maximum supported Python version
2. Runs pip with --dry-run to resolve dependencies
3. Extracts the vivarium_build_utils version from the output
4. Returns the version with 'v' prefix for git tagging convention

"""

import json
import os
import re
import subprocess
import sys

MINICONDA_DIR = "/svc-simsci/miniconda3"
IHME_PYPI = "https://artifactory.ihme.washington.edu/artifactory/api/pypi/pypi-shared/"


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
    cmd = f"""
    source {MINICONDA_DIR}/etc/profile.d/conda.sh
    conda activate py{env_version}
    uv pip install --dry-run . --extra-index-url {IHME_PYPI}simple/ --index-strategy unsafe-best-match
    """

    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
        # NOTE: uv evidently sends output to stderr
        return result.stderr
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to run pip dry-run: {e}", file=sys.stderr)
        print(f"STDERR: {e.stderr}", file=sys.stderr)
        sys.exit(1)


def _extract_vbu_version(dry_run_output: str) -> str:
    """Extract vivarium_build_utils version from pip dry-run output."""
    # uv logs installed packages like:
    #   + vivarium-build-utils==1.2.3
    # OR
    #   + vivarium-build-utils @ git+https://github.com/ihmeuw/vivarium_build_utils.git@<HASH>
    for line in dry_run_output.split("\n"):
        if "+ vivarium-build-utils" in line:
            # Check for pinned version first
            version_match = re.search(
                r"vivarium-build-utils==([0-9]+\.[0-9]+\.[0-9]+[^\s]*)", line
            )
            if version_match:
                version = version_match.group(1)
                # Add 'v' prefix for git tagging convention
                return f"v{version}"

            # Check for git reference
            git_match = re.search(
                r"vivarium-build-utils @ git\+https://github\.com/ihmeuw/vivarium_build_utils\.git@([a-f0-9]+)",
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

    python_version = _get_max_python_version()
    dry_run_output = _run_pip_dry_run(python_version)
    vbu_version = _extract_vbu_version(dry_run_output)
    
    # NOTE: We must print the vbu_version for Jenkins to capture it!
    print(vbu_version)
    return vbu_version


if __name__ == "__main__":
    main()
