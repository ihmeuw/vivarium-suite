"""Author a pipeline by adding steps to one pipeline object.

A :class:`Pipeline` holds a run's output root, its run name, the
:class:`~vivarium.dagger.backend.wiring.FileRegistry` that wires its steps, the
directory for its pytask state, and the steps added so far. A pipeline definition makes
one pipeline, adds each step with a method such as :meth:`Pipeline.add_python_step`, and
ends with ``return p``::

    def pipeline() -> Pipeline:
        p = Pipeline(HERE / "output", run_name="example")
        p.add_python_step(make_population, inputs={}, outputs="population.parquet")
        p.add_python_step(
            total, inputs={"population": "population.parquet"}, outputs="total.csv"
        )
        return p

Each step method calls the step function of the same name, such as
:func:`~vivarium.dagger.frontend.python_step.python_step`, with the pipeline's registry,
so a step added through the pipeline is the same task, with the same checks and
messages, as one compiled against the registry by hand, except that a missing or empty
name is reported under the step method's own name. The pipeline also refuses a step
whose name, or one of whose outputs, another step in the pipeline already has, when the
step is added; when both apply, the error names the output. :meth:`Pipeline.add_task` is
the lower layer: it adds a task that was compiled with a step function directly or built
by hand.

A step must be added after the steps it depends on. A step method refuses, when the step
is added, an input key that is neither a root input nor an output of a step already
added. Every other dependency, such as a notebook step's source notebook or an input of
a task added with :meth:`Pipeline.add_task`, is checked when the pipeline is built, not
when the step is added: :func:`~vivarium.dagger.frontend.execution.run`,
:func:`~vivarium.dagger.frontend.execution.preview` and the ``dagger`` command refuse a
pipeline in which a step depends on a step added after it, before any step runs.

A pipeline can still change after it is made, so
:func:`~vivarium.dagger.frontend.execution.run`,
:func:`~vivarium.dagger.frontend.execution.preview` and the ``dagger`` command first
freeze it into a private snapshot of the steps added so far, and read only that
snapshot. A step added after a run has started is in the next run, not that one.

By default the pipeline keeps its pytask state in ``<output root>/.dagger/<run name>``,
outside the run's output directory ``<output root>/<run name>``. Two pipelines under one
output root whose run names name different directories, neither inside the other, keep
separate state, so they do not share a lock, and write their outputs to different
directories. The pipeline refuses a run directory that a symbolic link puts outside the
output root or inside ``.dagger``, but it does not compare one run's directory with
another's. So it does not check nested run names, such as ``a`` and ``a/b``, different
spellings of one directory, such as ``run1`` and ``./run1``, or a symbolic link that
makes one run's output directory the other's, or a directory inside it.
"""

import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from pytask import PathNode

from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import FileKey, FileRegistry, WiringError
from vivarium.dagger.frontend.command_step import _step_name
from vivarium.dagger.frontend.command_step import command_step as compile_command_step
from vivarium.dagger.frontend.notebook_step import NotebookParameter
from vivarium.dagger.frontend.notebook_step import notebook_step as compile_notebook_step
from vivarium.dagger.frontend.python_step import python_step as compile_python_step
from vivarium.dagger.frontend.root_inputs import RootInputs
from vivarium.dagger.frontend.simulation_step import (
    simulation_step as compile_simulation_step,
)

__all__ = ["STATE_DIR", "Pipeline"]

STATE_DIR = ".dagger"
"""The directory under the output root that holds each run's default state directory."""


class Pipeline:
    """Collect the steps of one run, to run or preview them as a pipeline.

    Pass the pipeline to :func:`~vivarium.dagger.frontend.execution.run` or
    :func:`~vivarium.dagger.frontend.execution.preview`, or return it from a pipeline
    definition for the ``dagger`` command.

    Parameters
    ----------
    output_root
        Directory under which the run's outputs live, in ``<output root>/<run name>``.
        A relative path is resolved against the current directory when the pipeline is
        made.
    run_name
        Names the run's output directory and, by default, its state directory, so two
        runs under one output root do not collide.
    root_inputs
        Source files the pipeline reads but does not produce, keyed by the file key
        a step names as an input, as for
        :class:`~vivarium.dagger.backend.wiring.FileRegistry`. Any
        :data:`~vivarium.dagger.frontend.root_inputs.RootInputs` mapping can be passed
        here, such as the one that an input helper like
        :func:`~vivarium.dagger.frontend.root_inputs.location_inputs` returns, or
        several merged into one.
    state
        Absolute directory for the run's pytask configuration, state database, lockfile
        and run lock. Defaults to ``<output root>/.dagger/<run name>``, with the output
        root resolved.
    resources
        The definition's default compute resources for a run through Jobmon, such as
        ``{"project": "proj_simscience", "memory": 4}``. They override
        :data:`~vivarium.dagger.constants.DEFAULT_RESOURCES` key by key, and the
        ``dagger`` command's options and each step's own ``resources=`` override them in
        turn. A run in this process ignores them. Defaults to none.

    Raises
    ------
    ValueError
        If ``run_name`` is an absolute path, even one inside the output root, since the
        default state directory is built from the run name; if the run's output
        directory, ``<output root>/<run name>``, is not strictly inside the output root
        (as for an empty run name, ``.`` or ``..``), or is :data:`STATE_DIR` or a
        directory inside it (as for the run name ``.dagger``), since the run's outputs
        would then share a directory with the state of every run; or if ``state`` is a
        relative path, which would move the run's state with the current directory. The
        output directory is checked twice: first with ``.`` and ``..`` collapsed, and
        then with symbolic links followed as the operating system follows them, against
        the output root and against :data:`STATE_DIR` with its own links followed. So a
        run directory that links to a directory inside the output root is accepted, also
        when that directory does not exist yet, and one that links outside the output
        root, to the output root itself, or to :data:`STATE_DIR` or into it is refused.
        The message names the run name or the state path. A message from the check with
        symbolic links followed also names the run's output directory as the run name
        gives it, and the directory that it is with symbolic links followed. The
        pipeline does not check a link from one run's output directory to another's, or
        a :data:`STATE_DIR` that is a link to a directory inside a run's output
        directory. A :data:`STATE_DIR` that is a link to the run's output directory
        itself is refused.
    TypeError
        If ``resources`` is not a mapping.
    """

    def __init__(
        self,
        output_root: Path | str,
        run_name: str,
        root_inputs: RootInputs | None = None,
        state: Path | str | None = None,
        resources: Mapping[str, Any] | None = None,
    ) -> None:
        root = Path(output_root).resolve()
        _check_run_name(root, run_name)
        if state is None:
            state_dir = root / STATE_DIR / run_name
        else:
            state_dir = Path(state)
            if not state_dir.is_absolute():
                raise ValueError(
                    "Pipeline state must be an absolute path, got the relative path "
                    f"{str(state_dir)!r}."
                )
        if resources is not None and not isinstance(resources, Mapping):
            raise TypeError(
                "Pipeline resources must be a mapping, got "
                f"{type(resources).__name__} {resources!r}."
            )
        self._output_root = root
        self._run_name = run_name
        self._state = state_dir
        self._registry = FileRegistry(root, run_name, root_inputs)
        self._resources: dict[str, Any] = {} if resources is None else dict(resources)
        self._tasks: list[Task] = []
        self._producers: dict[FileKey, str] = {}
        # Outputs of steps added with add_task, by resolved path: the registry does not
        # know their keys.
        self._task_outputs: dict[Path, str] = {}
        # Which method added each step, by step name, for 'dagger show'.
        self._kinds: dict[str, str] = {}

    @property
    def output_root(self) -> Path:
        """The resolved directory under which the run's output directory lives."""
        return self._output_root

    @property
    def run_name(self) -> str:
        """The name of the run's output directory under the output root."""
        return self._run_name

    @property
    def state(self) -> Path:
        """The absolute directory that holds the run's pytask state and its run lock."""
        return self._state

    @property
    def resources(self) -> Mapping[str, Any]:
        """A copy of the definition's default compute resources, empty if none were given."""
        return dict(self._resources)

    def add_python_step(
        self,
        target: Callable[..., pd.DataFrame | Mapping[str, pd.DataFrame]],
        *,
        inputs: Mapping[str, FileKey],
        outputs: FileKey | Mapping[str, FileKey],
        resources: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> None:
        """Add a step that calls ``target`` and saves each DataFrame it returns.

        The step runs in the pipeline's own environment. It is compiled by
        :func:`~vivarium.dagger.frontend.python_step.python_step` with the pipeline's
        registry, which describes each parameter in full.

        The pipeline checks the step's name and outputs before anything is registered,
        and a step that is refused is not added.

        Parameters
        ----------
        target
            A function returning a pandas DataFrame, or a dict of them when ``outputs``
            is a mapping, defined at the top level of an importable module. Each input
            reaches it, under its parameter name, as the input's file path.
        inputs
            Maps each of ``target``'s parameter names to the file key of an output
            of a step already added, or of a root input.
        outputs
            The file key of the step's output, or a mapping of output names to file
            keys; with a mapping, ``target`` returns a dict with exactly those names.
            Each key's suffix, ``.parquet`` or ``.csv``, names the format its DataFrame
            is saved in.
        resources
            Compute resources for this step in a run through Jobmon, such as
            ``{"memory": 16, "runtime": 7200}``. They override the pipeline's
            ``resources`` key by key.
        name
            The step's name. Defaults to ``outputs`` when it is a key, or to its one key
            when it is a mapping with one entry, and is required when the mapping has
            more than one.

        Raises
        ------
        ValueError
            If the step's name is the name of a step already added; the message starts
            with ``Step '<name>':`` and says to give each step a different ``name=``.
            If ``name`` is empty, the message is ``add_python_step: name must be a
            non-empty string, got ''.`` With no ``name``, an empty ``outputs`` and an
            ``outputs`` mapping with more than one entry each raise a message that starts
            with ``add_python_step:``.
            Also for each case in which
            :func:`~vivarium.dagger.frontend.python_step.python_step` raises it.
        vivarium.dagger.backend.wiring.WiringError
            If an ``outputs`` key is an output of a step already added; the message starts
            with ``Step '<name>':`` and names the key and the step that produces it.
            If the step's name and one of its outputs are both already in the
            pipeline, this error is raised, not the ``ValueError`` for the name.
            Also for each case in which
            :func:`~vivarium.dagger.frontend.python_step.python_step` raises it, such as
            an input key that no step already added produces and that is not a root
            input.
        """
        named: Mapping[str, FileKey] = (
            outputs if isinstance(outputs, Mapping) else {"outputs": outputs}
        )
        keys = list(named.values())
        self._check_new(_step_name(named, name, "add_python_step"), keys)
        task = compile_python_step(
            target,
            inputs=inputs,
            outputs=outputs,
            registry=self._registry,
            resources=resources,
            name=name,
        )
        self._add(task, keys, "python")

    def add_notebook_step(
        self,
        notebook: Path | str,
        *,
        executed: FileKey,
        env: str,
        inputs: Mapping[str, FileKey] | None = None,
        outputs: Mapping[str, FileKey] | None = None,
        parameters: Mapping[str, NotebookParameter] | None = None,
        resources: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> None:
        """Add a step that runs ``notebook`` with papermill in the environment ``env``.

        The step is compiled by
        :func:`~vivarium.dagger.frontend.notebook_step.notebook_step` with the pipeline's
        registry, which describes each parameter, the notebook's ``parameters`` cell and
        the failure message in full.

        The pipeline checks the step's name and outputs before anything is registered,
        and a step that is refused is not added.

        Parameters
        ----------
        notebook
            Path to the source notebook, an existing ``.ipynb`` file. Every name passed
            in ``inputs``, ``outputs`` or ``parameters`` must be assigned in its first
            cell tagged ``parameters``.
        executed
            The file key, ending in ``.ipynb``, of the executed notebook, which is an
            output of the step.
        env
            The environment the notebook runs in: a conda env or venv name, or a path to
            its prefix. It must have ``papermill`` and ``ipykernel`` installed.
        inputs
            Maps notebook parameter names to the file keys of outputs of steps
            already added, or of root inputs. Each reaches the notebook as its path, as
            a ``str``.
        outputs
            Maps notebook parameter names to the file keys of the data files the
            notebook writes. Each reaches the notebook as its path, as a ``str``.
        parameters
            Maps notebook parameter names to scalar values: a ``str``, a real number, a
            ``bool`` or ``None``.
        resources
            Compute resources for this step in a run through Jobmon. They override the
            pipeline's ``resources`` key by key.
        name
            The step's name. Defaults to ``executed``.

        Raises
        ------
        ValueError
            If the step's name is the name of a step already added; the message starts
            with ``Step '<name>':`` and says to give each step a different ``name=``.
            If ``name`` is empty, the message is ``add_notebook_step: name must be a
            non-empty string, got ''.``
            Also for each case in which
            :func:`~vivarium.dagger.frontend.notebook_step.notebook_step` raises it.
        vivarium.dagger.backend.wiring.WiringError
            If ``executed`` or an ``outputs`` key is an output of a step already added;
            the message starts with ``Step '<name>':`` and names the key and the step
            that produces it. If the step's name and one of its outputs are both
            already in the pipeline, this error is raised, not the ``ValueError`` for
            the name. Also for each case in which
            :func:`~vivarium.dagger.frontend.notebook_step.notebook_step` raises it.
        """
        keys = [executed, *(outputs or {}).values()]
        self._check_new(_step_name({"executed": executed}, name, "add_notebook_step"), keys)
        task = compile_notebook_step(
            notebook,
            executed=executed,
            registry=self._registry,
            env=env,
            inputs=inputs,
            outputs=outputs,
            parameters=parameters,
            resources=resources,
            name=name,
        )
        self._add(task, keys, "notebook")

    def add_command_step(
        self,
        command: str,
        *,
        outputs: Mapping[str, FileKey],
        inputs: Mapping[str, FileKey] | None = None,
        env: str | None = None,
        resources: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> None:
        """Add a step that runs the shell ``command`` under ``/bin/sh``.

        The step is compiled by
        :func:`~vivarium.dagger.frontend.command_step.command_step` with the pipeline's
        registry, which describes the placeholders, the shell, ``env`` and the outputs
        in full.

        The pipeline checks the step's name and outputs before anything is registered,
        and a step that is refused is not added.

        Parameters
        ----------
        command
            The shell command, with a ``{name}`` placeholder for each input or output
            path it uses. Write ``{{`` and ``}}`` for a literal brace.
        outputs
            Maps placeholder names to the file keys of the files the command writes.
            It must have at least one entry.
        inputs
            Maps placeholder names to the file keys of outputs of steps already
            added, or of root inputs.
        env
            The environment whose ``bin`` directories go first on ``PATH`` while the
            command runs: a conda env or venv name, or a path to its prefix. ``None``
            keeps the ``PATH`` of the process that runs the command.
        resources
            Compute resources for this step in a run through Jobmon. They override the
            pipeline's ``resources`` key by key.
        name
            The step's name. Defaults to the ``outputs`` key when ``outputs`` has one
            entry, and is required when it has more than one.

        Raises
        ------
        ValueError
            If the step's name is the name of a step already added; the message starts
            with ``Step '<name>':`` and says to give each step a different ``name=``.
            If ``name`` is empty, the message is ``add_command_step: name must be a
            non-empty string, got ''.`` With no ``name``, an empty ``outputs`` and an
            ``outputs`` mapping with more than one entry each raise a message that starts
            with ``add_command_step:``.
            Also for each case in which
            :func:`~vivarium.dagger.frontend.command_step.command_step` raises it.
        vivarium.dagger.backend.wiring.WiringError
            If an ``outputs`` key is an output of a step already added; the message
            starts with ``Step '<name>':`` and names the key and the step that produces
            it. If the step's name and one of its outputs are both already in the
            pipeline, this error is raised, not the ``ValueError`` for the name. Also
            for each case in which
            :func:`~vivarium.dagger.frontend.command_step.command_step` raises it.
        """
        keys = list(outputs.values())
        self._check_new(_step_name(outputs, name, "add_command_step"), keys)
        task = compile_command_step(
            command,
            outputs=outputs,
            registry=self._registry,
            inputs=inputs,
            env=env,
            resources=resources,
            name=name,
        )
        self._add(task, keys, "command")

    def add_simulation_step(
        self,
        *,
        spec: Path | str,
        branches: Path | str,
        outputs: FileKey,
        env: str,
        options: Mapping[str, str | int | float | bool],
        artifact: FileKey | None = None,
        resources: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> None:
        """Add a step that runs a vivarium simulation with ``psimulate run``.

        The step is compiled by
        :func:`~vivarium.dagger.frontend.simulation_step.simulation_step` with the
        pipeline's registry, which describes the command, the run directory, the options
        and the failure message in full. psimulate runs only on a cluster compute node.

        The pipeline checks the step's name and outputs before anything is registered,
        and a step that is refused is not added.

        Parameters
        ----------
        spec
            Path to the model specification, an existing file.
        branches
            Path to the branches file, an existing file.
        outputs
            The file key of the run directory, the step's output. A step that names
            this key as an input receives the run directory's path.
        env
            The environment psimulate runs in: a conda env or venv name, or a path to
            its prefix. It must have ``vivarium-psimulate`` installed with its
            ``cluster`` extra.
        options
            The options of ``psimulate run``, keyed by long option name with ``_`` for
            ``-``, such as ``peak_memory``. ``project`` is required.
        artifact
            The file key of the model's artifact, an output of a step already added
            or a root input. Without it, psimulate reads the artifact the model
            specification names.
        resources
            Compute resources for this step in a run through Jobmon. They must cover the
            whole simulation, and they override the pipeline's ``resources`` key by key.
        name
            The step's name. Defaults to ``outputs``.

        Raises
        ------
        ValueError
            If the step's name is the name of a step already added; the message starts
            with ``Step '<name>':`` and says to give each step a different ``name=``.
            If ``name`` is empty, the message is ``add_simulation_step: name must be a
            non-empty string, got ''.``
            Also for each case in which
            :func:`~vivarium.dagger.frontend.simulation_step.simulation_step` raises it.
        vivarium.dagger.backend.wiring.WiringError
            If ``outputs`` is an output of a step already added; the message starts
            with ``Step '<name>':`` and names the key and the step that produces it.
            If the step's name and one of its outputs are both already in the
            pipeline, this error is raised, not the ``ValueError`` for the name.
            Also for each case in which
            :func:`~vivarium.dagger.frontend.simulation_step.simulation_step` raises it.
        """
        keys = [outputs]
        self._check_new(_step_name({"outputs": outputs}, name, "add_simulation_step"), keys)
        task = compile_simulation_step(
            spec=spec,
            branches=branches,
            outputs=outputs,
            registry=self._registry,
            env=env,
            options=options,
            artifact=artifact,
            resources=resources,
            name=name,
        )
        self._add(task, keys, "simulation")

    def add_task(self, task: Task) -> None:
        """Add ``task``, a step that is already compiled, to the end of the pipeline.

        This is the lower layer under the step methods, for a task compiled with a step
        function directly, such as
        :func:`~vivarium.dagger.frontend.python_step.python_step`, or built by hand as a
        :class:`~vivarium.dagger.backend.task.Task`. The pipeline's registry is private
        and knows only the root inputs and the outputs of steps added with the step
        methods. So :func:`~vivarium.dagger.frontend.execution.preview` names by path
        each output of such a task and each input whose key the pipeline does not know,
        and a step added with a step method cannot name one of its outputs as an input.

        The pipeline checks the task before anything changes, and a task that is refused
        is not added. Each output that is a file path node is compared, by its resolved
        path, with the outputs of the steps already added; any other output is not. The
        ``dagger`` command refuses a pipeline in which two steps have an output that is
        not a path node with the same node name.

        A hand-built task's outputs do not go through the pipeline's registry, so they
        are not checked to be inside the run's output directory, or not inside another
        step's output, as the step methods' outputs are.

        Add a task after the steps it depends on. This is not checked when the task is
        added. It is checked when the pipeline is built:
        :func:`~vivarium.dagger.frontend.execution.run`,
        :func:`~vivarium.dagger.frontend.execution.preview` and the ``dagger`` command
        refuse a pipeline in which a step depends on a step added after it, and name
        both steps.

        Parameters
        ----------
        task
            The task to add. Its name is the step's name.

        Raises
        ------
        ValueError
            If the task's name is the name of a step already added; the message starts
            with ``Step '<name>':`` and says to give each step a different ``name=``.
        vivarium.dagger.backend.wiring.WiringError
            If one of the task's output paths is an output of a step already added,
            whichever way that step was added; the message starts with
            ``Step '<name>':`` and names the output, by its file key if the pipeline's
            registry knows the path and otherwise by path, and the step that produces
            it. If the task's name and one of its outputs are both already in the
            pipeline, this error is raised, not the ``ValueError`` for the name.
        """
        paths = _output_paths(task)
        for path in paths:
            producer = self._producer_of(path)
            if producer is not None:
                key = self._registry.key_for(path)
                output = repr(key) if key is not None else repr(str(path))
                raise WiringError(
                    f"Step {task.name!r}: output {output} is already produced by step "
                    f"{producer!r}; each output can have only one producing step."
                )
        self._check_name(task.name)
        self._tasks.append(task)
        self._kinds[task.name] = "task"
        for path in paths:
            self._task_outputs[path] = task.name

    def _check_new(self, step: str, keys: Iterable[FileKey]) -> None:
        """Refuse a step whose name or one of whose outputs an added step already has."""
        # An empty output key as the default name is the step function's error to report.
        if not step:
            return
        # Outputs first: two unnamed steps that produce one key also share a default
        # name, and a different name= would not fix that.
        for key in keys:
            producer = self._producers.get(key)
            if producer is None and self._task_outputs:
                producer = self._task_outputs.get(self._registry.path_for(key))
            if producer is not None:
                raise WiringError(
                    f"Step {step!r}: output {key!r} is already produced by step "
                    f"{producer!r}; each file key can have only one producing step."
                )
        self._check_name(step)

    def _check_name(self, step: str) -> None:
        """Refuse a step named like a step already added."""
        if any(task.name == step for task in self._tasks):
            raise ValueError(
                f"Step {step!r}: a step named {step!r} was already added to the pipeline; "
                "give each step a different name= to tell them apart."
            )

    def _producer_of(self, path: Path) -> str | None:
        """Return the name of the step already added that produces ``path``, if any."""
        key = self._registry.key_for(path)
        if key is not None and key in self._producers:
            return self._producers[key]
        return self._task_outputs.get(path)

    def _add(self, task: Task, keys: Iterable[FileKey], kind: str) -> None:
        """Append ``task`` of ``kind`` and record it as the producer of each of ``keys``."""
        self._tasks.append(task)
        self._kinds[task.name] = kind
        for key in keys:
            self._producers[key] = task.name


@dataclass(frozen=True)
class _PipelineSnapshot:
    """Hold the fixed tasks, state directory, registry, resources and step kinds of a run."""

    tasks: Sequence[Task]
    # The absolute directory for pytask's configuration, state database and lockfile.
    root: Path
    # Without a registry, preview names files by path.
    registry: FileRegistry | None = None
    resources: Mapping[str, Any] = field(default_factory=dict)
    # Each step's kind by step name, as the Pipeline method that added it names it:
    # "python", "notebook", "command", "simulation", or "task" for add_task.
    kinds: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Store ``tasks`` as a tuple; reject a relative root or non-mapping resources."""
        object.__setattr__(self, "tasks", tuple(self.tasks))
        if not isinstance(self.root, Path):
            raise TypeError(
                f"Pipeline root must be a pathlib.Path, got {type(self.root).__name__} "
                f"{self.root!r}."
            )
        if not self.root.is_absolute():
            raise ValueError(
                f"Pipeline root must be absolute, got the relative path {str(self.root)!r}."
            )
        if not isinstance(self.resources, Mapping):
            raise TypeError(
                "Pipeline resources must be a mapping, got "
                f"{type(self.resources).__name__} {self.resources!r}."
            )


def freeze(pipeline: Pipeline) -> _PipelineSnapshot:
    """Return a snapshot of the steps added to ``pipeline`` so far.

    The verbs and the ``dagger`` command call this before they read a pipeline's steps,
    so that each call reads one fixed set of steps. A step added to ``pipeline`` after
    this call does not reach the snapshot it returned.

    Parameters
    ----------
    pipeline
        The pipeline to freeze.

    Returns
    -------
        A new snapshot on each call, whose ``tasks`` are the tasks of the steps added so
        far, as a tuple in the order they were added, whose ``root`` is
        :attr:`Pipeline.state`, whose ``registry`` is the pipeline's registry, whose
        ``resources`` are a copy of :attr:`Pipeline.resources`, and whose ``kinds`` map
        each step's name to the kind of step the pipeline added it as.

    Raises
    ------
    TypeError
        If ``pipeline`` is not a :class:`Pipeline`. The message names the type it got
        and says that a ``Pipeline`` is expected.
    """
    if not isinstance(pipeline, Pipeline):
        raise TypeError(f"Expected a Pipeline, got {type(pipeline).__name__}.")
    return _PipelineSnapshot(
        tasks=tuple(pipeline._tasks),
        root=pipeline.state,
        registry=pipeline._registry,
        resources=pipeline.resources,
        kinds=dict(pipeline._kinds),
    )


def _output_paths(task: Task) -> list[Path]:
    """Return the resolved path of each of ``task``'s outputs that is a file path node."""
    return [
        node.path.resolve()
        for node in task.outputs.values()
        if isinstance(node, PathNode) and isinstance(node.path, Path)
    ]


def _check_run_name(root: Path, run_name: str) -> None:
    """Refuse a run name whose output directory is not its own directory under ``root``."""
    if Path(run_name).is_absolute():
        # An absolute name inside the root would make the default state, built from the
        # run name, a directory inside the run's own output directory.
        raise ValueError(
            f"Pipeline run name {run_name!r} is an absolute path. Give a relative run "
            "name, such as 'run1', that names a directory under the output root "
            f"{root}."
        )
    run_dir = Path(os.path.normpath(root / run_name))
    state_root = root / STATE_DIR
    if root not in run_dir.parents:
        raise ValueError(
            f"Pipeline run name {run_name!r} puts the run's output directory at {run_dir}, "
            f"which is not inside the output root {root}. Give a relative run name, such "
            "as 'run1', that names a directory under the output root."
        )
    if run_dir == state_root or state_root in run_dir.parents:
        raise ValueError(
            f"Pipeline run name {run_name!r} puts the run's output directory at {run_dir}, "
            f"which is {STATE_DIR!r} or inside it; that directory holds the state of every "
            f"run under the output root {root}. Give a run name outside {STATE_DIR!r}, such "
            "as 'run1'."
        )
    # The join is not normalized first, so a link before '..' is followed as the operating
    # system follows it. Path.resolve raises on a link loop in Python 3.11 and 3.12;
    # os.path.realpath does not.
    real_run_dir = Path(os.path.realpath(root / run_name))
    real_state_root = Path(os.path.realpath(state_root))
    followed = (
        f"Pipeline run name {run_name!r} puts the run's output directory at {run_dir}, "
        f"which is {real_run_dir} with symbolic links followed"
    )
    if root not in real_run_dir.parents:
        raise ValueError(
            f"{followed}; that is not a directory inside the output root {root}. Give a "
            "run name whose output directory, with symbolic links followed, is inside the "
            "output root."
        )
    if real_run_dir == real_state_root or real_state_root in real_run_dir.parents:
        state = str(state_root)
        if real_state_root != state_root:
            state += f" ({real_state_root} with symbolic links followed)"
        raise ValueError(
            f"{followed}; that is the state directory {state} or inside it, and that "
            f"directory holds the state of every run under the output root {root}. Give a "
            "run name whose output directory, with symbolic links followed, is outside "
            "the state directory."
        )
