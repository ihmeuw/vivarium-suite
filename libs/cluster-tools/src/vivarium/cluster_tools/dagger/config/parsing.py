"""
=====================
Workflow Step Parsing
=====================

YAML -> API kwargs translation for workflow steps. Each step type has a
parser that turns the raw YAML dict into the kwargs of its matching
interface API function. Parsers do YAML-shape validation (required
fields, unsupported ``args`` keys, ``command``/``type`` conflicts) inline.

Also exposes workflow-level entry points: :func:`parse_step_from_yaml`
and :func:`load_workflow_config`. ParsedStep -> YAML dict serialization
lives in
:mod:`vivarium.cluster_tools.dagger.config.serialization`.

"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, cast

from vivarium.cluster_tools.dagger.config.config import (
    DEFAULT_MAX_ATTEMPTS,
    ParsedStep,
    ResourceConfig,
    WorkflowConfig,
)

_SIMULATION_SUPPORTED_ARGS: set[str] = {
    "model_specification",
    "branch_configuration",
    "artifact_path",
    "backup_freq",
    "sim_verbosity",
}
_PYTEST_SUPPORTED_ARGS: set[str] = {"path", "k", "runslow"}
_PYTHON_SUPPORTED_ARGS: set[str] = {"path", "positional_args", "keyword_args"}
_NOTEBOOK_SUPPORTED_ARGS: set[str] = {"path", "parameters", "output_path", "cwd"}


def _parse_common_step_fields(
    data: dict[str, Any],
    *,
    project: str,
    queue: str,
) -> tuple[str, ResourceConfig]:
    """Validate and extract the fields required by every step type.

    Checks that the step has a ``name``, a ``resources`` block, and that the
    block contains ``memory_gb``. Returns the step name and the constructed
    :class:`ResourceConfig`.
    """
    if "name" not in data:
        raise ValueError("Step: missing required field 'name'.")
    step_name = data["name"]
    if "resources" not in data:
        raise ValueError(f"Step '{step_name}': missing required field 'resources'.")
    resources_data = data["resources"]
    if "memory_gb" not in resources_data:
        raise ValueError(f"Step '{step_name}': missing required 'memory_gb' in 'resources'.")
    resources = ResourceConfig.from_dict(
        resources_data, workflow_project=project, workflow_queue=queue
    )
    return step_name, resources


def _get_required_args(
    data: dict[str, Any], step_name: str, required_fields: tuple[str, ...]
) -> dict[str, Any]:
    """Return ``data['args']`` after asserting the block and all ``required_fields`` are present."""
    if "args" not in data:
        raise ValueError(f"Step '{step_name}': missing required field 'args'.")
    args = cast(dict[str, Any], data["args"])
    for field in required_fields:
        if field not in args:
            raise ValueError(f"Step '{step_name}': missing required '{field}' in 'args'.")
    return args


def resolve_step_type(step_dict: dict[str, Any]) -> str:
    """Pick the step-type key for ``step_dict``.

    Dispatch rules:

    - A top-level ``command`` field always resolves to ``"bash"``;
      :func:`parse_bash_step_from_yaml` enforces the rest of the
      bash-step schema (including any conflicting ``type``).
    - Otherwise, an explicit ``type`` is used.
    - A step with neither ``command`` nor ``type`` is rejected.
    """
    if "command" in step_dict:
        return "bash"
    if "name" not in step_dict:
        raise ValueError("Step: missing required field 'name'.")
    step_name = step_dict["name"]
    if "type" not in step_dict:
        raise ValueError(
            f"Step '{step_name}': must specify either a 'command' field or a "
            f"'type' field. Supported types: {sorted(STEP_TYPE_YAML_PARSERS)}."
        )
    step_type: str = step_dict["type"]
    if step_type not in STEP_TYPE_YAML_PARSERS:
        raise ValueError(
            f"Step '{step_name}': unsupported type '{step_type}'. "
            f"Must be one of: {sorted(STEP_TYPE_YAML_PARSERS)}."
        )
    return step_type


def parse_bash_step_from_yaml(
    data: dict[str, Any],
    output_directory: Path,
    *,
    project: str,
    queue: str,
) -> dict[str, Any]:
    """Parse a raw bash-step YAML dict into API kwargs.

    The YAML form for a bash step requires a top-level ``command`` field.
    The optional ``type`` field, when present, must be ``"bash"``. No
    ``args:`` block is accepted.

    Examples
    --------
    YAML configuration::

        steps:
          - name: post_analysis
            command: python scripts/analyze.py --input /results
            environment: analysis_env
            resources:
              memory_gb: 20
              runtime: "02:00:00"
              cores: 2
    """
    step_name, resources = _parse_common_step_fields(data, project=project, queue=queue)
    if "args" in data:
        raise ValueError(f"Step '{step_name}': bash steps do not support an 'args' block.")
    if "command" not in data:
        raise ValueError(f"Step '{step_name}': missing required field 'command'.")
    explicit_type = data.get("type", "bash")
    if explicit_type != "bash":
        raise ValueError(
            f"Step '{step_name}': cannot specify both 'command' and "
            f"'type: {explicit_type}'. When 'command' is set, 'type' "
            "must be omitted or set to 'bash'."
        )
    return {
        "name": step_name,
        "resources": resources,
        "command": data["command"],
        "output_directory": output_directory,
        "environment": data.get("environment"),
    }


def parse_simulation_step_from_yaml(
    data: dict[str, Any],
    output_directory: Path,
    *,
    project: str,
    queue: str,
) -> dict[str, Any]:
    """Parse a raw simulation-step YAML dict into API kwargs.

    Examples
    --------
    YAML configuration::

        steps:
          - name: model_sims
            type: simulation
            resources:
              memory_gb: 3
              runtime: "24:00:00"
            args:
              model_specification: /path/to/model.yaml
              branch_configuration: /path/to/branches.yaml
              artifact_path: /path/to/artifact.hdf
              backup_freq: 1800
              sim_verbosity: 1
    """
    step_name, resources = _parse_common_step_fields(data, project=project, queue=queue)
    args = _get_required_args(
        data, step_name, ("model_specification", "branch_configuration")
    )
    _check_supported_args(args, step_name, _SIMULATION_SUPPORTED_ARGS)

    kwargs: dict[str, Any] = {
        "name": step_name,
        "resources": resources,
        "output_directory": output_directory,
        "environment": data.get("environment"),
        "model_specification": Path(args["model_specification"]).resolve(),
        "branch_configuration": Path(args["branch_configuration"]).resolve(),
    }
    if "artifact_path" in args:
        kwargs["artifact_path"] = Path(args["artifact_path"]).resolve()
    if "backup_freq" in args:
        kwargs["backup_freq"] = args["backup_freq"]
    if "sim_verbosity" in args:
        kwargs["sim_verbosity"] = args["sim_verbosity"]
    return kwargs


def parse_pytest_step_from_yaml(
    data: dict[str, Any],
    output_directory: Path,
    *,
    project: str,
    queue: str,
) -> dict[str, Any]:
    """Parse a raw pytest-step YAML dict into API kwargs.

    Optional ``args`` keys: ``path``, ``k``, ``runslow``. At least one of
    ``path`` or ``k`` must be provided. ``path`` may be a single string or a list of strings.

    Examples
    --------
    YAML configuration::

        steps:
          - name: unit_tests
            type: pytest
            resources:
              memory_gb: 8
              runtime: "01:00:00"
              cores: 4
            args:
              path: tests/
              k: "test_foo"
              runslow: true

    Multiple paths::

        steps:
          - name: unit_and_integration
            type: pytest
            resources:
              memory_gb: 8
              runtime: "01:00:00"
            args:
              path:
                - tests/unit
                - tests/integration
    """
    step_name, resources = _parse_common_step_fields(data, project=project, queue=queue)
    args = data.get("args", {}) or {}
    _check_supported_args(args, step_name, _PYTEST_SUPPORTED_ARGS)

    kwargs: dict[str, Any] = {
        "name": step_name,
        "resources": resources,
        "output_directory": output_directory,
    }
    if "environment" in data:
        kwargs["environment"] = data["environment"]
    if "path" in args:
        raw_path = args["path"]
        if isinstance(raw_path, list):
            kwargs["path"] = [str(Path(p).resolve()) for p in raw_path]
        else:
            kwargs["path"] = str(Path(raw_path).resolve())
    if "k" in args:
        kwargs["k"] = args["k"]
    if "runslow" in args:
        kwargs["runslow"] = args["runslow"]
    return kwargs


def parse_python_step_from_yaml(
    data: dict[str, Any],
    output_directory: Path,
    *,
    project: str,
    queue: str,
) -> dict[str, Any]:
    """Parse a raw python-step YAML dict into API kwargs.

    Required ``args`` key: ``path`` (a ``.py`` script). Optional ``args``
    keys: ``positional_args`` (list of scalars) and ``keyword_args`` (dict
    of identifier-keyed scalars).

    Examples
    --------
    YAML configuration::

        steps:
          - name: postprocess
            type: python
            resources:
              memory_gb: 8
              runtime: "00:30:00"
            args:
              path: scripts/postprocess.py
              positional_args:
                - "foo"
                - "bar"
              keyword_args:
                input_dir: /mnt/results/model_29
                verbose: true
                num_workers: 4
    """
    step_name, resources = _parse_common_step_fields(data, project=project, queue=queue)
    args = _get_required_args(data, step_name, ("path",))
    _check_supported_args(args, step_name, _PYTHON_SUPPORTED_ARGS)
    kwargs: dict[str, Any] = {
        "name": step_name,
        "resources": resources,
        "output_directory": output_directory,
        "environment": data.get("environment"),
        "path": str(Path(args["path"]).resolve()),
    }
    if "positional_args" in args:
        kwargs["positional_args"] = args["positional_args"]
    if "keyword_args" in args:
        kwargs["keyword_args"] = args["keyword_args"]
    return kwargs


def parse_notebook_step_from_yaml(
    data: dict[str, Any],
    output_directory: Path,
    *,
    project: str,
    queue: str,
) -> dict[str, Any]:
    """Parse a raw notebook-step YAML dict into API kwargs.

    Required ``args`` keys: ``path`` (input ``.ipynb``) and ``output_path``
    (executed ``.ipynb``). Optional ``args`` keys: ``parameters`` (dict of
    identifier-keyed scalars injected into the notebook) and ``cwd``
    (working directory for execution; defaults to the parent of ``path``).

    Examples
    --------
    YAML configuration::

        steps:
          - name: post_notebook_neonatal
            type: notebook
            resources:
              memory_gb: 20
              runtime: "02:00:00"
            args:
              path: tests/model_notebooks/results/neonatal.ipynb
              output_path: /mnt/results/run_29/executed/neonatal.ipynb
              parameters:
                model_dir: /mnt/results/run_29
                year: 2020
                verbose: true
    """
    step_name, resources = _parse_common_step_fields(data, project=project, queue=queue)
    args = _get_required_args(data, step_name, ("path", "output_path"))
    _check_supported_args(args, step_name, _NOTEBOOK_SUPPORTED_ARGS)

    kwargs: dict[str, Any] = {
        "name": step_name,
        "resources": resources,
        "output_directory": output_directory,
        "path": Path(args["path"]).resolve(),
        "output_path": Path(args["output_path"]).resolve(),
    }
    if "environment" in data:
        kwargs["environment"] = data["environment"]
    if "parameters" in args:
        kwargs["parameters"] = args["parameters"]
    if "cwd" in args:
        kwargs["cwd"] = Path(args["cwd"]).resolve()
    return kwargs


def _check_supported_args(args: dict[str, Any], step_name: str, supported: set[str]) -> None:
    """Raise ValueError if args contains keys not in ``supported``."""
    unsupported = set(args) - supported
    if unsupported:
        raise ValueError(
            f"Step '{step_name}': unsupported args {sorted(unsupported)}. "
            f"Supported args: {sorted(supported)}."
        )


STEP_TYPE_YAML_PARSERS: dict[str, Callable[..., dict[str, Any]]] = {
    "bash": parse_bash_step_from_yaml,
    "simulation": parse_simulation_step_from_yaml,
    "pytest": parse_pytest_step_from_yaml,
    "python": parse_python_step_from_yaml,
    "notebook": parse_notebook_step_from_yaml,
}
"""Maps each YAML ``step_type`` to its YAML -> API kwargs parser."""


def parse_step_from_yaml(
    raw: dict[str, Any],
    output_directory: Path,
    *,
    project: str,
    queue: str,
) -> ParsedStep:
    """Build a ParsedStep from a raw YAML step dict.

    Dispatches to the matching per-type parser to produce ``api_kwargs``
    and tags the result with the resolved ``step_type`` for downstream
    dispatch (task building, YAML serialization).
    """
    step_type = resolve_step_type(raw)
    api_kwargs = STEP_TYPE_YAML_PARSERS[step_type](
        raw, output_directory, project=project, queue=queue
    )
    return ParsedStep(
        step_type=step_type,
        name=api_kwargs["name"],
        api_kwargs=api_kwargs,
    )


def load_workflow_config(
    path: Path,
    *,
    name: str | None = None,
    project: str | None = None,
    queue: str | None = None,
    output_directory: Path | None = None,
    default_environment: str | None = None,
    max_attempts: int | None = None,
) -> WorkflowConfig:
    """Load a WorkflowConfig from YAML, merging CLI overrides.

    CLI arguments take precedence over values in the YAML file. Validates
    that ``name``, ``project``, ``queue``, and ``output_directory`` are
    provided by at least one source.

    Parameters
    ----------
    path
        Path to the workflow YAML configuration file.
    name
        CLI override for the workflow name.
    project
        CLI override for the project field.
    queue
        CLI override for the queue field.
    output_directory
        CLI override for the output directory.
    default_environment
        CLI override for the default_environment field.
    max_attempts
        CLI override for the maximum number of Jobmon task attempts.

    Raises
    ------
    ValueError
        If ``name``, ``project``, ``queue``, or ``output_directory`` cannot
        be resolved from either the YAML file or CLI arguments.
    """
    workflow = WorkflowConfig.parse_yaml_file(path)

    resolved_name = name or workflow.get("name")
    resolved_project = project or workflow.get("project")
    resolved_queue = queue or workflow.get("queue")
    resolved_output_directory = output_directory or (
        Path(workflow["output_directory"]).resolve()
        if "output_directory" in workflow
        else None
    )

    if not resolved_name:
        raise ValueError(
            "Workflow name is required. Provide it in the config file or via --name/-n."
        )
    if not resolved_project:
        raise ValueError(
            "Project is required. Provide it in the config file or via --project/-P."
        )
    if not resolved_queue:
        raise ValueError(
            "Queue is required. Provide it in the config file or via --queue/-q."
        )
    if not resolved_output_directory:
        raise ValueError(
            "Output directory is required. Provide it in the config file "
            "or via --output-directory/-o."
        )

    steps = [
        parse_step_from_yaml(
            raw,
            output_directory=resolved_output_directory,
            project=resolved_project,
            queue=resolved_queue,
        )
        for raw in workflow["steps"]
    ]

    return WorkflowConfig(
        name=resolved_name,
        project=resolved_project,
        queue=resolved_queue,
        output_directory=resolved_output_directory,
        default_environment=default_environment or workflow.get("default_environment"),
        steps=steps,
        max_attempts=max_attempts or workflow.get("max_attempts", DEFAULT_MAX_ATTEMPTS),
    )
