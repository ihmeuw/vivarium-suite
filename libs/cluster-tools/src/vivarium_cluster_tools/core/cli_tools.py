"""
================
Shared CLI tools
================

"""

import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click
import yaml
from vivarium.engine.interface.cli_tools import verbose_option

# NOTE: The argument type hints for the cli wrappers are not precise; they should
# be type-hinted using Protocols. However, the functions being wrapped are never
# expected to be called in a type-hinted context (because they are used via CLI).
CLIFunction = Callable[..., None]
Decorator = Callable[[CLIFunction], CLIFunction]


def with_verbose_and_pdb(func: CLIFunction) -> CLIFunction:
    func = verbose_option(
        help="Configure logging verbosity of main runner for a parallel simulation."
    )(func)
    func = click.option(
        "--pdb",
        "with_debugger",
        is_flag=True,
        help="Drop into python debugger if an error occurs.",
    )(func)
    return func


def with_sim_verbosity(func: CLIFunction) -> CLIFunction:
    func = click.option(
        "-s",
        "sim_verbosity",
        count=True,
        help="Increase per-simulation logging verbosity. Use -s for INFO and -ss for DEBUG.",
    )(func)
    func = click.option(
        "--sim-verbosity",
        "sim_verbosity_deprecated",
        type=click.Choice(["0", "1", "2"]),
        default=None,
        hidden=True,
        help="Deprecated. Use -s/-ss instead.",
    )(func)
    return func


def resolve_sim_verbosity(sim_verbosity: int, sim_verbosity_deprecated: str | None) -> int:
    """Resolve per-simulation verbosity from the new ``-s`` count and the
    deprecated ``--sim-verbosity`` value option.

    Returns the resolved verbosity count and emits a deprecation warning if the
    old value form was used. Raises a click.UsageError if verbosity is supplied
    both as ``-s``/``-ss`` and via the deprecated ``--sim-verbosity`` option.
    """
    if sim_verbosity_deprecated is not None:
        if sim_verbosity > 0:
            raise click.UsageError(
                "Per-simulation verbosity was provided both as '-s'/'-ss' and via "
                "the deprecated '--sim-verbosity' option. Use only '-s'/'-ss'."
            )
        warnings.warn(
            "The '--sim-verbosity' option is deprecated; use '-s' (INFO) or '-ss' "
            "(DEBUG) instead.",
            FutureWarning,
            stacklevel=2,
        )
        return int(sim_verbosity_deprecated)
    return sim_verbosity


def coerce_to_full_path(
    ctx: click.Context, param: click.Parameter | None, value: str | None
) -> Path | None:
    if value is not None:
        return Path(value).resolve()
    return None


def pass_shared_options(shared_options: list[Decorator]) -> Decorator:
    """Allows the user to supply a list of click options to apply to a command."""

    def _pass_shared_options(func: CLIFunction) -> CLIFunction:
        # add all the shared options to the command
        for option in shared_options:
            func = option(func)
        return func

    return _pass_shared_options


class MinutesOrNone(click.ParamType[float | None]):
    """Click param type to allow user to set time in minutes or None."""

    name = "minutesornone"

    def convert(
        self, value: str, param: click.Parameter | None, ctx: click.Context | None
    ) -> float | None:
        """Converts the value to float seconds from minutes.

        If conversion fails, calls the `fail` method from `click.ParamType`.
        """
        try:
            if value.lower() == "none":
                return None
            # Convert minutes to seconds
            return float(value) * 60.0
        except ValueError:
            self.fail(f"{value!r} is not a valid float or 'none'", param, ctx)


MINUTES_OR_NONE = MinutesOrNone()


def load_run_config(ctx: click.Context, param: click.Parameter, value: str | None) -> None:
    """Eager callback for ``--run-config``.  Loads a YAML file and injects its
    values as defaults for the current command.

    * Options are set via ``ctx.default_map`` so Click's own type coercion,
      callbacks, and validation still apply.
    * Arguments (positional params) are handled by setting their ``default``
      and marking them as not required so Click does not complain about
      missing positional values.
    """
    if value is None:
        return

    config_path = Path(value)
    try:
        config: dict[str, Any] = yaml.safe_load(config_path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise click.BadParameter(f"Failed to parse YAML config file: {exc}", param=param)

    if not isinstance(config, dict):
        raise click.BadParameter(
            "Run config file must contain a YAML mapping (key: value pairs).",
            param=param,
        )

    # Map user-friendly config keys to internal parameter names where
    # positional arguments have been deprecated in favor of keyword options.
    _CONFIG_KEY_ALIASES: dict[str, str] = {
        "model_specification": "model_specification_opt",
        "branch_configuration": "branch_configuration_opt",
        "results_root": "results_root_opt",
    }

    # Remap aliased keys before validation.
    remapped: dict[str, Any] = {}
    for key, val in config.items():
        remapped[_CONFIG_KEY_ALIASES.get(key, key)] = val
    config = remapped

    # Validate that every key maps to a known parameter on this command.
    valid_names = {
        parameter.name for parameter in ctx.command.params if parameter.name is not None
    }
    # Also accept the user-friendly aliases as valid.
    valid_for_display = valid_names | set(_CONFIG_KEY_ALIASES.keys())
    unknown = set(config) - valid_names
    if unknown:
        raise click.BadParameter(
            f"Unrecognized config keys: {', '.join(sorted(unknown))}. "
            f"Valid keys for this command: {', '.join(sorted(valid_for_display))}",
            param=param,
        )

    # Separate arguments from options.
    arg_names = {
        parameter.name
        for parameter in ctx.command.params
        if isinstance(parameter, click.Argument)
    }

    # For options, use default_map so CLI values automatically win.
    option_defaults = {key: value for key, value in config.items() if key not in arg_names}
    ctx.default_map = {**(ctx.default_map or {}), **option_defaults}

    # For arguments, set the default and relax the required flag so Click
    # doesn't error when they aren't provided on the command line.
    for parameter in ctx.command.params:
        if isinstance(parameter, click.Argument) and parameter.name in config:
            parameter.default = config[parameter.name]
            parameter.required = False


def with_run_config(func: CLIFunction) -> CLIFunction:
    """Decorator that adds the ``--run-config`` option to a Click command."""
    return click.option(
        "--run-config",
        "-c",
        type=click.Path(exists=True, dir_okay=False),
        default=None,
        callback=load_run_config,
        is_eager=True,
        expose_value=False,
        help="Path to a YAML configuration file. Keys use the same "
        "snake_case names as CLI parameters (e.g., peak_memory, "
        "max_runtime, result_directory). Values in this file serve "
        "as defaults and are overridden by any argument provided on "
        "the command line.",
    )(func)


def with_workflow_config(func: CLIFunction) -> CLIFunction:
    """Decorator that adds the ``--config/-c`` option for workflow YAML files."""
    return click.option(
        "--config",
        "-c",
        "config_path",
        type=click.Path(exists=True, dir_okay=False),
        required=True,
        callback=coerce_to_full_path,
        is_eager=True,
        help="Path to the workflow configuration YAML file.",
    )(func)


def resolve_deprecated_positional(
    positional_value: Any,
    option_value: Any,
    param_name: str,
    option_flag: str,
) -> Any:
    """Resolve a parameter that can be provided as a positional arg (deprecated)
    or as a keyword option (preferred).

    Returns the resolved value and emits a deprecation warning if the positional
    form was used.  Raises ``click.UsageError`` if both forms are provided.
    """
    if positional_value is not None and option_value is not None:
        raise click.UsageError(
            f"'{param_name}' was provided both as a positional argument and "
            f"as the '{option_flag}' option. Use only the option form."
        )
    if positional_value is not None:
        warnings.warn(
            f"Passing '{param_name}' as a positional argument is deprecated. "
            f"Use '{option_flag}' instead.",
            FutureWarning,
            stacklevel=2,
        )
        return positional_value
    return option_value
