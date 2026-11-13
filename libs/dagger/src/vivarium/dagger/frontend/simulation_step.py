"""Author a pipeline step that runs a vivarium simulation with psimulate.

:func:`simulation_step` compiles one ``psimulate run`` into a single backend
:class:`~vivarium.dagger.backend.task.Task` whose ``run`` is a shell command. The command
runs in the step's declared environment, which
:func:`~vivarium.dagger.backend.build.run_command` puts first on ``PATH``, so that
environment needs ``vivarium-psimulate`` with its ``cluster`` extra. psimulate runs only
on a cluster compute node, and it submits and waits for its own Jobmon workflow, so the
step holds its node, or its local process, for the whole simulation.

psimulate writes each run to a new timestamped directory. The step instead keeps one
fixed run directory, the path its ``produces`` key resolves to. psimulate writes into a
scratch directory beside it, ``<run directory>.psimulate``. A check then confirms that
every simulation finished, moves the run to the run directory, replacing the previous
one, and writes a sentinel file into it last. The run directory is the step's product,
and its state is the sentinel's content, which is new on every run. A consumer names the
same key and receives the run directory's path. When a simulation did not finish, the
step fails, the previous complete run stays in the run directory, and the partial run
stays in the scratch directory until the step runs again.

The command, including every option and path, is hashed with the step, so a changed
option marks the step stale. The spec and branches files are declared inputs, so an edit
to either marks it stale too. Code identity defaults to always-stale, as for
:func:`~vivarium.dagger.frontend.python_step.python_step`, so in this cut a simulation
step re-runs on every build.
"""

import difflib
import hashlib
import shlex
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pytask import PathNode

from vivarium.dagger.backend.build import (
    COLLECT_SIMULATION_SCRIPT,
    COLLECT_SIMULATION_VARIABLE,
    describe_exit,
)
from vivarium.dagger.backend.nodes import RunDirectoryNode, file_node
from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import ArtifactKey, ArtifactRegistry, WiringError
from vivarium.dagger.frontend._collect_simulation import (
    _MESSAGE_FILE,
    _SENTINEL,
    has_complete_run,
    previous_run_sentence,
)

_SPEC_INPUT = "__spec__"
"""Task input name under which the model specification's file node is attached."""

_BRANCHES_INPUT = "__branches__"
"""Task input name under which the branches file's node is attached."""

_ARTIFACT_INPUT = "__artifact__"
"""Task input name under which the resolved artifact node is attached, when given."""

_RUN_OUTPUT = "__run__"
"""Task output name under which the run directory's node is attached."""

_SCRATCH_SUFFIX = ".psimulate"
"""Appended to the run directory's name to give the scratch directory psimulate writes to."""

_VALUE_OPTIONS = frozenset(
    {
        "backup_freq",
        "hardware",
        "max_attempts",
        "max_runtime",
        "max_workers",
        "peak_memory",
        "project",
        "queue",
        "slack_channel",
        "slack_tag",
    }
)
"""The options of ``psimulate run`` that take a value, given as a str, int or float."""

_FLAG_OPTIONS = frozenset({"no_slack"})
"""The options of ``psimulate run`` that are bare flags, given as a bool."""

_STEP_OPTIONS = {
    "artifact_path": "pass the artifact's key as artifact=",
    "branch_configuration": "pass the branches file as branches=",
    "model_specification": "pass the model specification as spec=",
    "result_directory": "the step sets the result directory itself",
    "run_config": "the step sets the run configuration itself",
}
"""The options of ``psimulate run`` the step sets itself, with what to do instead."""


def simulation_step(
    *,
    spec: Path | str,
    branches: Path | str,
    produces: ArtifactKey,
    registry: ArtifactRegistry,
    env: str,
    options: Mapping[str, str | int | float | bool],
    artifact: ArtifactKey | None = None,
    resources: dict[str, Any] | None = None,
    name: str | None = None,
) -> Task:
    """Compile a ``psimulate run`` into one task whose product is its run directory.

    The command, run from any directory, does the following, each part only if the one
    before it succeeded:

    1. creates the run directory's parent if it is missing, and removes and creates
       again the scratch directory ``<run directory>.psimulate``;
    2. runs ``psimulate run -M <spec> -B <branches> [-i <artifact>] -o <scratch>``
       followed by the option flags, with ``psimulate`` found on ``PATH``, that is, in
       ``env``;
    3. runs a check on the scratch and run directories with ``env``'s own ``python -I``.
       The check confirms that psimulate wrote one run in which every simulation
       finished, moves it to the run directory, replacing the previous run only then,
       and writes the sentinel last. When a simulation did not finish, it fails the
       step, keeps the previous complete run, and leaves the partial run in the scratch
       directory.

    Every path in the command is absolute, and every value is shell-quoted. The command
    names the check's script as ``"$DAGGER_COLLECT_SIMULATION"``, which dagger sets when
    it runs the step, and ends with a comment that holds a hash of the script, so an edit
    to the check changes the command and the step is stale. Run outside dagger, without
    that variable, the command stops with a message that names it.

    Parameters
    ----------
    spec
        Path to the model specification, a file that must exist. It is a declared input
        of the step.
    branches
        Path to the branches file, a file that must exist. It is a declared input of the
        step.
    produces
        The artifact key for the run directory, registered with ``registry`` as the
        step's product. A step that names this key as an input gets an edge to this
        step and receives the run directory's path: a ``pathlib.Path`` in a
        :func:`~vivarium.dagger.frontend.python_step.python_step` target, and a ``str``
        in a :func:`~vivarium.dagger.frontend.notebook_step.notebook_step` notebook. The
        directory holds psimulate's run, with ``results/<metric>/<task id>.parquet``,
        ``metadata/`` and ``logs/``. It is replaced on every run of the step, so no other
        step may write under it.
    registry
        The producer/consumer registry that resolves keys to shared nodes.
    env
        The environment psimulate runs in: a conda env or venv name, or a path to its
        prefix. It must have ``vivarium-psimulate`` installed with its ``cluster`` extra.
        Resolved when the pipeline is built or previewed, not here. Pass ``sys.prefix``
        to run psimulate in the pipeline's own environment.
    options
        The options of ``psimulate run``, keyed by long option name with ``_`` for
        ``-``, such as ``peak_memory`` for ``--peak-memory``. ``project`` is required.
        ``backup_freq``, ``hardware``, ``max_attempts``, ``max_runtime``,
        ``max_workers``, ``peak_memory``, ``project``, ``queue``, ``slack_channel`` and
        ``slack_tag`` take a ``str``, ``int`` or ``float``, and are passed as the
        option followed by the value, a number as its ``str()``. ``no_slack`` takes a
        ``bool``: ``True`` passes the bare ``--no-slack`` and ``False`` leaves it out.
        psimulate sends a Slack message when the run ends unless ``no_slack`` is
        ``True``. The flags are sorted by key, so equal options give an identical
        command in any order.
    artifact
        The artifact key of the model's artifact, which the registry resolves to the
        producing step's node or a declared root input. It becomes an input of the step
        and its path is passed to psimulate as ``-i``. Without it, psimulate reads the
        artifact the model specification names, which is not a declared input.
    resources
        Jobmon-shaped compute resources, carried on the task for the cluster bridge to
        read later. They must cover the whole simulation, since psimulate waits for its
        own workflow.
    name
        The step's name, which becomes the task name. Defaults to ``produces``.

    Returns
    -------
        One :class:`~vivarium.dagger.backend.task.Task` whose ``run`` is the command,
        whose ``env`` is ``env``, whose ``resources`` are ``resources`` or ``{}``, whose
        inputs are the file nodes of ``spec`` under ``"__spec__"`` and of ``branches``
        under ``"__branches__"`` and, when ``artifact`` is given, the node the registry
        resolves it to under ``"__artifact__"``, and whose one output, under
        ``"__run__"``, is the
        :class:`~vivarium.dagger.backend.nodes.RunDirectoryNode` the registry registers
        for ``produces``, at the path ``produces`` resolves to.

    Raises
    ------
    vivarium.dagger.backend.wiring.WiringError
        If ``artifact`` resolves to no producer and no root input, or ``produces`` is a
        declared root input, resolves to the path of a different registered key, or is
        already registered as a file output. The message starts with
        ``Step '<name>':``, followed by the registry's message, which names the artifact
        key.
    ValueError
        Raised before anything is registered, with a message that starts with
        ``Step '<name>':``, if:

        - ``name`` or ``env`` is empty, or ``produces`` is empty or ends in ``/``;
        - ``produces`` resolves to a path that is not strictly inside the registry's
          output directory, such as ``.``, ``..`` or an absolute path outside it, since
          the step replaces that directory on every run; the message names ``produces``
          and the output directory, and says that ``produces`` must name a directory
          under the output directory;
        - ``spec`` or ``branches`` does not exist or is not a file (the message names
          the path);
        - ``options`` has no ``project`` key;
        - ``options`` has a key the step sets itself (``model_specification``,
          ``branch_configuration``, ``artifact_path``, ``result_directory``,
          ``run_config``); the message names the key and says to pass ``spec=``,
          ``branches=`` or ``artifact=`` instead, or that the step sets it itself;
        - ``options`` has any other key that is not allowed; the message names the key,
          lists the allowed keys in sorted order, and ends with
          ``Did you mean '<key>'?`` when an allowed key is close;
        - an option's value has the wrong type: ``no_slack`` is not a ``bool``, or any
          other option is a ``bool`` or not a ``str``, ``int`` or ``float``; the message
          names the key, the type it got and the type it expects;
        - ``no_slack`` is ``True`` together with ``slack_channel`` or ``slack_tag``, or
          ``slack_tag`` is given without ``slack_channel``, which psimulate refuses; the
          message names the keys.
    """
    step = produces if name is None else name
    if not produces or produces.endswith("/"):
        raise ValueError(
            f"Step '{step}': produces must be a non-empty artifact key that does not end "
            f"in '/', got {produces!r}."
        )
    run_dir = registry.path_for(produces)
    output_dir = registry.path_for(".")
    if output_dir not in run_dir.parents:
        raise ValueError(
            f"Step '{step}': produces {produces!r} is not inside the registry's output "
            f"directory {output_dir}; produces must name a directory under the output "
            "directory, since the step replaces that directory on every run."
        )
    if not step:
        raise ValueError(f"Step '{step}': name must be a non-empty string, got {step!r}.")
    if not env:
        raise ValueError(f"Step '{step}': env must be a non-empty string, got {env!r}.")
    spec_path = _existing_file(step, "spec", spec)
    branches_path = _existing_file(step, "branches", branches)
    _check_options(step, options)

    try:
        inputs: dict[str, PathNode] = {
            _SPEC_INPUT: file_node(spec_path),
            _BRANCHES_INPUT: file_node(branches_path),
        }
        artifact_path: Path | None = None
        if artifact is not None:
            artifact_node = registry.resolve_input(artifact)
            inputs[_ARTIFACT_INPUT] = artifact_node
            artifact_path = _node_path(artifact_node)
        run_node = registry.register_directory_output(produces, sentinel=_SENTINEL)
    except WiringError as error:
        raise WiringError(f"Step {step!r}: {error}") from None
    command = _psimulate_command(spec_path, branches_path, artifact_path, run_dir, options)
    # TODO(MIC-7760): let a step declare an opt-in code fingerprint so it can skip
    # when its code is unchanged; until then code_id defaults to always-stale.
    return Task(
        name=step,
        run=command,
        inputs=inputs,
        outputs={_RUN_OUTPUT: run_node},
        resources={} if resources is None else resources,
        env=env,
    )


def describe_simulation_failure(task: Task, error: BaseException) -> str | None:
    """Describe why a simulation step's command failed, from its scratch directory.

    Only the standard library reads the scratch directory, so this works in the
    pipeline's own environment, and the generated command is never part of the
    description. :func:`~vivarium.dagger.frontend.pipeline.run` uses it in the message of
    :class:`~vivarium.dagger.frontend.pipeline.StepFailed`.

    The command ran and failed when ``error`` is a
    :class:`subprocess.CalledProcessError`, from a local build, or when its ``exc_type``
    attribute is ``"subprocess.CalledProcessError"``, as for a failure reported from a
    Jobmon node. Any other error means the command did not run, so a message file in
    the scratch directory is from an earlier run and is not read.

    Parameters
    ----------
    task
        A task that failed. Only a task built by :func:`simulation_step` is described.
    error
        The exception the task failed with.

    Returns
    -------
        For a simulation step whose command ran and failed, when the check left its
        message in the scratch directory, that message, such as::

            psimulate finished 3 of 4 simulations; the partial run is at <run>. The
            previous complete run at <run directory> was kept.

        on one line. Otherwise the description says only that the command failed,
        since psimulate or the check may have been the part that failed. For a local
        ``CalledProcessError``::

            the simulation step's command failed (<exit>) before writing a message; its
            output is in pytask's report for the step, and psimulate's logs are under
            <scratch>. <previous run>

        on one line, where ``<exit>`` is
        :func:`~vivarium.dagger.backend.build.describe_exit` of the return code. For a
        failure reported from a Jobmon node, which carries no return code::

            the simulation step's command failed before writing a message; its output
            is in the step's report, and psimulate's logs are under <scratch>.
            <previous run>

        on one line. ``<previous run>`` is ``The previous complete run at <run
        directory> was kept.`` when the run directory holds its sentinel, and ``There
        was no previous run.`` otherwise.

        ``None`` for a task that is not a simulation step, and for a simulation step
        whose command did not run.
    """
    if not isinstance(task.run, str) or not _is_command_failure(error):
        return None
    if _SPEC_INPUT not in task.inputs or _BRANCHES_INPUT not in task.inputs:
        return None
    node = task.outputs.get(_RUN_OUTPUT)
    run_dir = _node_path(node) if isinstance(node, RunDirectoryNode) else None
    if run_dir is None:
        return None
    scratch = _scratch_for(run_dir)
    try:
        message = (scratch / _MESSAGE_FILE).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        message = ""
    if message:
        return message
    previous = previous_run_sentence(run_dir, has_complete_run(run_dir))
    if isinstance(error, subprocess.CalledProcessError):
        exit_status = describe_exit(error.returncode)
        return (
            f"the simulation step's command failed ({exit_status}) before writing a "
            "message; its output is in pytask's report for the step, and psimulate's "
            f"logs are under {scratch}. {previous}"
        )
    return (
        "the simulation step's command failed before writing a message; its output is "
        f"in the step's report, and psimulate's logs are under {scratch}. {previous}"
    )


def _check_options(step: str, options: Mapping[str, object]) -> None:
    """Raise ValueError for a missing project, a refused key, a wrong type or a Slack clash."""
    allowed = sorted(_VALUE_OPTIONS | _FLAG_OPTIONS)
    for key, value in options.items():
        if key in _STEP_OPTIONS:
            raise ValueError(
                f"Step '{step}': option {key!r} cannot be given; {_STEP_OPTIONS[key]}."
            )
        if key not in allowed:
            listed = ", ".join(repr(each) for each in allowed)
            close = difflib.get_close_matches(key, allowed, n=1)
            hint = f" Did you mean {close[0]!r}?" if close else ""
            raise ValueError(
                f"Step '{step}': option {key!r} is not allowed; the allowed options are "
                f"{listed}.{hint}"
            )
        if key in _FLAG_OPTIONS:
            valid = isinstance(value, bool)
            expected = "a bool"
        else:
            valid = isinstance(value, (str, int, float)) and not isinstance(value, bool)
            expected = "a str, int or float"
        if not valid:
            raise ValueError(
                f"Step '{step}': option {key!r} must be {expected}, got "
                f"{type(value).__name__}."
            )
    if "project" not in options:
        raise ValueError(
            f"Step '{step}': options must include 'project', the cluster project "
            "psimulate runs under."
        )
    if options.get("no_slack") is True:
        clash = [key for key in ("slack_channel", "slack_tag") if key in options]
        if clash:
            named = " and ".join(repr(key) for key in clash)
            raise ValueError(
                f"Step '{step}': option 'no_slack' is True together with {named}, which "
                "psimulate refuses; leave out no_slack or the Slack options."
            )
    if "slack_tag" in options and "slack_channel" not in options:
        raise ValueError(
            f"Step '{step}': option 'slack_tag' is given without 'slack_channel', which "
            "psimulate refuses; add slack_channel or leave out slack_tag."
        )


def _option_flags(options: Mapping[str, str | int | float | bool]) -> list[str]:
    """Return psimulate's arguments for ``options``, sorted by key."""
    flags: list[str] = []
    for key in sorted(options):
        value = options[key]
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                flags.append(flag)
        else:
            flags.extend([flag, str(value)])
    return flags


def _psimulate_command(
    spec: Path,
    branches: Path,
    artifact: Path | None,
    destination: Path,
    options: Mapping[str, str | int | float | bool],
) -> str:
    """Return the command that runs psimulate into the scratch directory and collects the run."""
    scratch = _scratch_for(destination)
    psimulate = ["psimulate", "run", "-M", str(spec), "-B", str(branches)]
    if artifact is not None:
        psimulate += ["-i", str(artifact)]
    psimulate += ["-o", str(scratch), *_option_flags(options)]
    # Bare `python` and the variable keep absolute env paths out of the hashed command,
    # and the script's hash keeps an edit to the check in it. -I keeps the working
    # directory and PYTHON* variables off sys.path, so no file in the directory pytask
    # runs from can replace a standard-library module in the check.
    check = f'"${{{COLLECT_SIMULATION_VARIABLE}:?is set by dagger when it runs the step}}"'
    collect = f"python -I {check} {shlex.quote(str(scratch))} {shlex.quote(str(destination))}"
    commands = [
        f"mkdir -p {shlex.quote(str(destination.parent))}",
        f"rm -rf {shlex.quote(str(scratch))}",
        f"mkdir {shlex.quote(str(scratch))}",
        shlex.join(psimulate),
        collect,
    ]
    return " && ".join(commands) + f"  # check {_collect_script_hash()}"


def _is_command_failure(error: BaseException) -> bool:
    """Return whether ``error`` says the step's command ran and exited with an error."""
    if isinstance(error, subprocess.CalledProcessError):
        return True
    return getattr(error, "exc_type", None) == "subprocess.CalledProcessError"


def _collect_script_hash() -> str:
    """Return a short hash of the check's script, which ends the step's command."""
    return hashlib.sha256(COLLECT_SIMULATION_SCRIPT.read_bytes()).hexdigest()[:12]


def _scratch_for(run_dir: Path) -> Path:
    """Return the scratch directory psimulate writes to, beside ``run_dir``."""
    return run_dir.with_name(run_dir.name + _SCRATCH_SUFFIX)


def _existing_file(step: str, label: str, path: Path | str) -> Path:
    """Return ``path`` resolved; raise ValueError if it is not an existing file."""
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise ValueError(
            f"Step '{step}': {label} {resolved} does not exist or is not a file."
        )
    return resolved


def _node_path(node: object) -> Path | None:
    """Return ``node``'s path if it is a PathNode on a :class:`pathlib.Path`."""
    if isinstance(node, PathNode) and isinstance(node.path, Path):
        return node.path
    return None
