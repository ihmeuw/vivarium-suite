"""
===========================
Workflow Step Serialization
===========================

ParsedStep -> YAML dict translation for workflow steps. Each step type has
a serializer that turns its ``api_kwargs`` (carried on a
:class:`~vivarium_cluster_tools.psimulate.workflow_config.config.ParsedStep`)
back into the dict shape expected in a workflow YAML file.

Also exposes :func:`workflow_config_to_dict`, the workflow-level entry
point that serializes a full
:class:`~vivarium_cluster_tools.psimulate.workflow_config.config.WorkflowConfig`.

"""

from __future__ import annotations

from typing import Any, Callable

from vivarium_cluster_tools.psimulate.workflow_config.config import (
    DEFAULT_BACKUP_FREQ_SECONDS,
    ParsedStep,
    WorkflowConfig,
)


def serialize_bash_step_to_yaml(step: ParsedStep) -> dict[str, Any]:
    """Serialize a bash-step ``ParsedStep`` to a YAML-ready dict."""
    kwargs = step.api_kwargs
    result: dict[str, Any] = {
        "name": kwargs["name"],
        "command": kwargs["command"],
        "resources": kwargs["resources"].to_dict(),
    }
    environment = kwargs.get("environment")
    if environment is not None:
        result["environment"] = environment
    return result


def serialize_simulation_step_to_yaml(step: ParsedStep) -> dict[str, Any]:
    """Serialize a simulation-step ``ParsedStep`` to a YAML-ready dict."""
    kwargs = step.api_kwargs
    result: dict[str, Any] = {
        "name": kwargs["name"],
        "type": "simulation",
        "resources": kwargs["resources"].to_dict(),
    }
    environment = kwargs.get("environment")
    if environment is not None:
        result["environment"] = environment

    args: dict[str, Any] = {
        "model_specification": str(kwargs["model_specification"]),
        "branch_configuration": str(kwargs["branch_configuration"]),
    }
    if kwargs.get("artifact_path") is not None:
        args["artifact_path"] = str(kwargs["artifact_path"])
    if "backup_freq" in kwargs and kwargs["backup_freq"] != DEFAULT_BACKUP_FREQ_SECONDS:
        args["backup_freq"] = kwargs["backup_freq"]
    if "sim_verbosity" in kwargs and kwargs["sim_verbosity"] != 0:
        args["sim_verbosity"] = kwargs["sim_verbosity"]

    result["args"] = args
    return result


def serialize_pytest_step_to_yaml(step: ParsedStep) -> dict[str, Any]:
    """Serialize a pytest-step ``ParsedStep`` to a YAML-ready dict."""
    kwargs = step.api_kwargs
    result: dict[str, Any] = {
        "name": kwargs["name"],
        "type": "pytest",
        "resources": kwargs["resources"].to_dict(),
    }
    environment = kwargs.get("environment")
    if environment is not None:
        result["environment"] = environment

    args: dict[str, Any] = {}
    if kwargs.get("path") is not None:
        args["path"] = kwargs["path"]
    if kwargs.get("k") is not None:
        args["k"] = kwargs["k"]
    if kwargs.get("runslow"):
        args["runslow"] = True

    result["args"] = args
    return result


def serialize_python_step_to_yaml(step: ParsedStep) -> dict[str, Any]:
    """Serialize a python-step ``ParsedStep`` to a YAML-ready dict."""
    kwargs = step.api_kwargs
    result: dict[str, Any] = {
        "name": kwargs["name"],
        "type": "python",
        "resources": kwargs["resources"].to_dict(),
    }
    environment = kwargs.get("environment")
    if environment is not None:
        result["environment"] = environment

    args: dict[str, Any] = {"path": kwargs["path"]}
    if kwargs.get("positional_args") is not None:
        args["positional_args"] = kwargs["positional_args"]
    if kwargs.get("keyword_args") is not None:
        args["keyword_args"] = kwargs["keyword_args"]
    result["args"] = args
    return result


def serialize_notebook_step_to_yaml(step: ParsedStep) -> dict[str, Any]:
    """Serialize a notebook-step ``ParsedStep`` to a YAML-ready dict."""
    kwargs = step.api_kwargs
    result: dict[str, Any] = {
        "name": kwargs["name"],
        "type": "notebook",
        "resources": kwargs["resources"].to_dict(),
    }
    environment = kwargs.get("environment")
    if environment is not None:
        result["environment"] = environment

    args: dict[str, Any] = {
        "path": str(kwargs["path"]),
        "output_path": str(kwargs["output_path"]),
    }
    if kwargs.get("parameters"):
        args["parameters"] = kwargs["parameters"]
    if kwargs.get("cwd") is not None:
        args["cwd"] = str(kwargs["cwd"])

    result["args"] = args
    return result


STEP_TYPE_YAML_SERIALIZERS: dict[str, Callable[[ParsedStep], dict[str, Any]]] = {
    "bash": serialize_bash_step_to_yaml,
    "simulation": serialize_simulation_step_to_yaml,
    "pytest": serialize_pytest_step_to_yaml,
    "python": serialize_python_step_to_yaml,
    "notebook": serialize_notebook_step_to_yaml,
}
"""Maps each YAML ``step_type`` to its ParsedStep -> YAML dict serializer."""


def workflow_config_to_dict(config: WorkflowConfig) -> dict[str, Any]:
    """Serialize a :class:`~vivarium_cluster_tools.psimulate.workflow_config.config.WorkflowConfig` to a dict suitable for YAML output."""
    result: dict[str, Any] = {
        "name": config.name,
        "project": config.project,
        "queue": config.queue,
        "output_directory": str(config.output_directory),
        "max_attempts": config.max_attempts,
    }
    if config.default_environment is not None:
        result["default_environment"] = config.default_environment
    result["steps"] = [
        STEP_TYPE_YAML_SERIALIZERS[step.step_type](step) for step in config.steps
    ]
    return result
