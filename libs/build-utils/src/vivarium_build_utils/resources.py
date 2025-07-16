"""Utilities for finding vivarium_build_utils resources."""

import os
from pathlib import Path


def get_resources_path() -> str:
    """Get the path to the vivarium_build_utils resources directory.

    Returns
    -------
        Path to the resources directory.

    Raises
    ------
        FileNotFoundError: If the resources directory cannot be found in the expected locations.
    """
    this_file = Path(__file__)

    # Use installed package resources if available and repository root otherwise
    # (for editable installs and Jenkins builds).
    prioritized_candidates = (
        this_file.parent / "resources",
        this_file.parents[2] / "resources",
    )
    resources_path = next((path for path in prioritized_candidates if path.exists()), None)

    if not resources_path:
        raise FileNotFoundError(
            f"Resources directory not found in expected locations: {prioritized_candidates}"
        )

    return str(resources_path)


def get_makefiles_path():
    """Get the path to the makefiles directory."""
    return os.path.join(get_resources_path(), "makefiles")
