"""
========================
Workflow Step Validation
========================

Per-step-type Python validation for workflow step kwargs. Each
``validate_<type>_step`` function is the single entry point that validates
all kwargs accepted by the matching ``get_<type>_step_tasks`` API function.
Shared helpers handle validation that's common across multiple step types.

"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from vivarium.cluster_tools.dagger.config.config import (
    DEFAULT_BACKUP_FREQ_SECONDS,
    ResourceConfig,
)
from vivarium.cluster_tools.dagger.config.utilities import check_scalar, validate_scalar_dict

_PYTHON_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
"""Notebook parameter keys must be valid Python identifiers because papermill
injects them as variable assignments in a notebook cell."""


def _validate_common(name: str, resources: ResourceConfig) -> None:
    """Validation shared by every step type: name + resource resolution."""
    if not name:
        raise ValueError("Step 'name' is required.")
    if not isinstance(resources.queue, str) or not isinstance(resources.project, str):
        raise ValueError(
            f"Step '{name}': resources 'queue' and 'project' must be "
            "configured. Set them at the step level or provide workflow-level defaults."
        )


def _validate_required_paths(name: str, paths: list[Path]) -> None:
    """Raise FileNotFoundError if any path does not exist."""
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"Step '{name}': path does not exist: {path}")


def _validate_positional_args(step_name: str, positional_args: Any) -> None:
    """Validate that positional_args is a list of scalar values."""
    if not isinstance(positional_args, list):
        raise ValueError(
            f"Step '{step_name}': 'positional_args' must be a list, "
            f"got {type(positional_args).__name__}."
        )
    for arg_index, item in enumerate(positional_args):
        check_scalar(
            item,
            label=f"positional_args[{arg_index}]",
            step_name=step_name,
            allow_none=False,
        )


def validate_bash_step(
    *,
    name: str,
    resources: ResourceConfig,
    command: str,
    environment: str | None = None,
) -> None:
    """Validate kwargs for :func:`~vivarium.cluster_tools.dagger.config.interface.get_bash_step_tasks`."""
    _validate_common(name, resources)
    if not command:
        raise ValueError(f"Step '{name}': 'command' is required.")


def validate_simulation_step(
    *,
    name: str,
    resources: ResourceConfig,
    model_specification: Path,
    branch_configuration: Path,
    environment: str | None = None,
    artifact_path: Path | None = None,
    backup_freq: float | None = DEFAULT_BACKUP_FREQ_SECONDS,
    sim_verbosity: int = 0,
) -> None:
    """Validate kwargs for :func:`~vivarium.cluster_tools.dagger.config.interface.get_simulation_step_tasks`."""
    _validate_common(name, resources)
    if not model_specification:
        raise ValueError(f"Step '{name}': simulation type requires 'model_specification'.")
    if not branch_configuration:
        raise ValueError(f"Step '{name}': simulation type requires 'branch_configuration'.")
    paths = [model_specification, branch_configuration]
    if artifact_path is not None:
        paths.append(artifact_path)
    _validate_required_paths(name, paths)


def validate_pytest_step(
    *,
    name: str,
    resources: ResourceConfig,
    environment: str | None = None,
    path: str | list[str] | None = None,
    k: str | None = None,
    runslow: bool = False,
) -> None:
    """Validate kwargs for :func:`~vivarium.cluster_tools.dagger.config.interface.get_pytest_step_tasks`."""
    _validate_common(name, resources)
    if not path and not k:
        raise ValueError(
            f"Step '{name}': pytest type requires at least one of 'path' or 'k'."
        )
    if path is not None:
        raw_paths = path if isinstance(path, list) else [path]
        _validate_required_paths(name, [Path(p) for p in raw_paths])


def validate_python_step(
    *,
    name: str,
    resources: ResourceConfig,
    path: str,
    environment: str | None = None,
    positional_args: list[Any] | None = None,
    keyword_args: dict[str, Any] | None = None,
) -> None:
    """Validate kwargs for :func:`~vivarium.cluster_tools.dagger.config.interface.get_python_step_tasks`."""
    _validate_common(name, resources)
    if not path:
        raise ValueError(f"Step '{name}': python type requires 'path' in args.")
    if not isinstance(path, str) or not path.endswith(".py"):
        raise ValueError(
            f"Step '{name}': 'path' must be a string ending with .py, got {path!r}."
        )
    if positional_args is not None:
        _validate_positional_args(name, positional_args)
    if keyword_args is not None:
        validate_scalar_dict(
            keyword_args,
            field_name="keyword_args",
            step_name=name,
        )
    _validate_required_paths(name, [Path(path)])


def validate_notebook_step(
    *,
    name: str,
    resources: ResourceConfig,
    path: Path,
    output_path: Path,
    environment: str | None = None,
    parameters: dict[str, Any] | None = None,
    cwd: Path | None = None,
) -> None:
    """Validate kwargs for :func:`~vivarium.cluster_tools.dagger.config.interface.get_notebook_step_tasks`."""
    _validate_common(name, resources)
    if not str(path).endswith(".ipynb"):
        raise ValueError(f"Step '{name}': 'path' must end with .ipynb, got {path!r}.")
    if not str(output_path).endswith(".ipynb"):
        raise ValueError(
            f"Step '{name}': 'output_path' must end with .ipynb, got {output_path!r}."
        )
    params = parameters if parameters is not None else {}
    validate_scalar_dict(params, field_name="parameters", step_name=name)
    for key in params:
        if not _PYTHON_IDENTIFIER_RE.match(key):
            raise ValueError(
                f"Step '{name}': parameter key {key!r} is not a valid "
                "Python identifier. Notebooks require parameter names that "
                "are valid Python identifiers (letters, digits, underscores; "
                "cannot start with a digit)."
            )
    _validate_required_paths(name, [path])
