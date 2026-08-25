"""
==================
Workflow Utilities
==================

Shared helpers used across the ``workflow_config`` package: filesystem
constants for resume markers, build-timestamp management, environment
resolution, and step-config scalar validation.

"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from vivarium.cluster_tools.core.jobmon import client
from vivarium.cluster_tools.core.jobmon.env import resolve_env_bin_path, resolve_env_prefix
from vivarium.cluster_tools.dagger.config.config import ResourceConfig

if TYPE_CHECKING:
    from jobmon.client.api import Tool
    from jobmon.client.task import Task

BUILD_TIMESTAMP_FILENAME = ".build_timestamp"
"""File written to a step's output directory to persist the build timestamp
so that repeat invocations against the same directory share the same
``model_name / timestamp`` layout."""

WORKFLOW_ARGS_FILENAME = ".workflow_args"
"""File written to the output directory to persist the Jobmon workflow_args
for resume support."""

CONFIGURATION_FILENAME = "configuration.yaml"
"""File written to the output directory holding the full, override-resolved
workflow configuration. Reloaded by ``dagger restart`` to resume the workflow."""


_SCALAR_TYPES: tuple[type, ...] = (str, int, float, bool)
"""Scalar value types accepted in step args (e.g. notebook ``parameters``,
python ``keyword_args`` / ``positional_args``)."""

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
"""Pattern for keys in scalar-dict step args (``keyword_args``, ``parameters``)."""


def ensure_output_directory_exists(output_directory: Path) -> None:
    """Create ``output_directory`` (and parents) if it does not yet exist."""
    output_directory.mkdir(parents=True, exist_ok=True)


def get_or_create_build_timestamp(output_directory: Path) -> str:
    """Return a stable build timestamp for ``output_directory``.

    Reads the persisted timestamp from a marker file under ``output_directory``
    if one exists; otherwise generates a fresh ``YYYY_MM_DD_HH_MM_SS`` stamp
    and writes it to the marker file. Subsequent calls against the same
    directory reuse the persisted value so that all tasks targeting it
    share the same ``model_name / timestamp`` layout.
    """
    timestamp_path = output_directory / BUILD_TIMESTAMP_FILENAME
    if timestamp_path.exists():
        return timestamp_path.read_text().strip()

    build_timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    output_directory.mkdir(parents=True, exist_ok=True)
    timestamp_path.write_text(build_timestamp)
    return build_timestamp


def resolve_step_env_prefix(
    *,
    name: str,
    environment: str | None,
) -> str:
    """Resolve a step's environment to an absolute filesystem prefix.

    ``environment`` may be a conda env name, a venv name, or a path to
    either's prefix (see
    :func:`~vivarium.cluster_tools.core.jobmon.env.resolve_env_prefix`).
    When unset, falls back to the runner's active venv (``VIRTUAL_ENV``),
    then the active conda env (``CONDA_DEFAULT_ENV``). The resolved env
    must not be conda's ``base``.

    Parameters
    ----------
    name
        The step's name (used in error messages).
    environment
        The step's explicit environment, if any.

    Returns
    -------
        The absolute filesystem prefix of the resolved environment,
        suitable for passing as ``env_prefix`` to Jobmon task builders.

    Raises
    ------
    ValueError
        If no non-base environment can be resolved.
    RuntimeError
        If the resolved env name has no matching filesystem prefix.
    """
    env = environment or os.environ.get("VIRTUAL_ENV") or os.environ.get("CONDA_DEFAULT_ENV")
    if not env or env == "base":
        raise ValueError(
            f"Step '{name}': a non-base environment is required. "
            "Set 'environment' on the step, 'default_environment' on the workflow, "
            "or activate a conda environment or venv before running."
        )
    return resolve_env_prefix(env)


def get_single_command_task(
    tool: Tool,
    *,
    name: str,
    resources: ResourceConfig,
    output_directory: Path,
    env_prefix: str,
    command: str,
) -> list[Task]:
    """Return a one-element ``list[Task]`` for a step that runs a single command in an env.

    The child inherits SLURM's stdout/stderr file descriptors via the
    ``stdout`` / ``stderr`` keys on the compute resources dict
    (configured by :meth:`NativeSpecification.to_jobmon_spec`), so its
    output flows directly to the per-step log files that the Jobmon GUI
    surfaces.
    """
    task_template = client.make_task_template(
        tool,
        template_name="workflow_command_step",
        command_template="PATH={env_bin_path}:$PATH {command}",
        node_args=["command", "env_bin_path"],
        task_args=[],
        op_args=[],
    )
    compute_resources = resources.to_native_specification(name).to_jobmon_spec(
        worker_logging_root=output_directory,
    )
    wrapped_command = (
        f'LOG=$(mktemp); {command} > "$LOG" 2>&1; RC=$?; '
        f'cat "$LOG"; [ "$RC" -ne 0 ] && cat "$LOG" >&2; '
        f'rm -f "$LOG"; exit "$RC"'
    )
    return [
        client.create_task(
            task_template,
            name=name,
            compute_resources=compute_resources,
            env_bin_path=resolve_env_bin_path(env_prefix),
            command=wrapped_command,
        )
    ]


def check_scalar(
    value: object,
    *,
    label: str,
    step_name: str,
    allow_none: bool = True,
) -> None:
    """Validate that *value* is a scalar type.

    Parameters
    ----------
    value
        The value to check.
    label
        Human-readable label for error messages (e.g. ``"positional_args[0]"``).
    step_name
        Name of the owning step, used in error messages.
    allow_none
        Whether ``None`` is an acceptable value.
    """
    allowed = (*_SCALAR_TYPES, type(None)) if allow_none else _SCALAR_TYPES
    if not isinstance(value, allowed):
        raise ValueError(
            f"Step '{step_name}': {label} must be a scalar type "
            f"({', '.join(t.__name__ for t in allowed)}), "
            f"got {type(value).__name__}."
        )


def validate_scalar_dict(
    configuration: object,
    *,
    field_name: str,
    step_name: str,
    allow_none_values: bool = True,
) -> None:
    """Validate ``configuration`` is a dict of identifier-keyed scalar values.

    Parameters
    ----------
    configuration
        The untrusted value to validate (typically a sub-dict from a step's
        YAML ``args``).
    field_name
        Name of the field being validated, used in error messages
        (e.g. ``"keyword_args"`` or ``"parameters"``).
    step_name
        Name of the owning step, used in error messages.
    allow_none_values
        Whether ``None`` is an acceptable value (treated as a flag for
        keyword args; treated as YAML ``null`` for notebook parameters).
    """
    if not isinstance(configuration, dict):
        raise ValueError(
            f"Step '{step_name}': '{field_name}' must be a dict, "
            f"got {type(configuration).__name__}."
        )
    for key, value in configuration.items():
        if not isinstance(key, str) or not _IDENTIFIER_RE.match(key):
            raise ValueError(
                f"Step '{step_name}': {field_name} key {key!r} is not a valid "
                "identifier. Keys must be alphanumeric, have dashes, or underscores."
            )
        check_scalar(
            value,
            label=f"{field_name}['{key}']",
            step_name=step_name,
            allow_none=allow_none_values,
        )
