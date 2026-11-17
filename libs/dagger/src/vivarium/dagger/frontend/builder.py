"""Author a pipeline by adding steps to one pipeline object.

A :class:`Pipeline` holds a run's output root, its run name, the
:class:`~vivarium.dagger.backend.wiring.FileRegistry` that wires its steps, the
directory for its pytask state, and the steps added so far. A pipeline definition makes
one pipeline, adds each step with a method such as :meth:`Pipeline.add_python_step`, and
ends with ``return p``::

    def pipeline() -> Pipeline:
        p = Pipeline(HERE / "output", run_name="example")
        p.add_python_step(make_population, inputs={}, produces="population.parquet")
        p.add_python_step(
            total, inputs={"population": "population.parquet"}, produces="total.csv"
        )
        return p

Each step method calls the step function of the same name, such as
:func:`~vivarium.dagger.frontend.python_step.python_step`, with the pipeline's registry,
so a step added through the pipeline is the same task, with the same checks and
messages, as one compiled against the registry by hand. The pipeline also refuses a step
whose name, or one of whose products, another step in the pipeline already has, when the
step is added; when both apply, the error names the product. A step that reads a
file must still be added after the step that produces it. :meth:`Pipeline.add_task` is
the lower layer: it adds a task that was compiled with a step function directly or built
by hand.

A pipeline can still change after it is made, so
:func:`~vivarium.dagger.frontend.execution.run`,
:func:`~vivarium.dagger.frontend.execution.preview` and the ``dagger`` command first
freeze it into a private snapshot of the steps added so far, and read only that
snapshot. A step added after a run has started is in the next run, not that one.

By default the pipeline keeps its pytask state in ``<output root>/.dagger/<run name>``,
outside the run's output directory ``<output root>/<run name>``. Two pipelines with
different run names under one output root therefore write their products to different
directories and keep separate state, so they do not share a lock.
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
        Directory under which the run's products live, in ``<output root>/<run name>``.
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
        directory, ``<output root>/<run name>`` with ``.`` and ``..`` collapsed
        (symlinks are not followed), is not strictly inside the output root (as for an
        empty run name, ``.`` or ``..``), or is :data:`STATE_DIR` or a directory inside
        it (as for the run name ``.dagger``), since the run's products would then share
        a directory with the state of every run; or if ``state`` is a relative path,
        which would move the run's state with the current directory. The message names
        the run name or the state path.
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
        target: Callable[..., pd.DataFrame],
        *,
        inputs: Mapping[str, FileKey],
        produces: FileKey,
        resources: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> None:
        """Add a step that calls ``target`` and saves the DataFrame it returns.

        The step runs in the pipeline's own environment. It is compiled by
        :func:`~vivarium.dagger.frontend.python_step.python_step` with the pipeline's
        registry, which describes each parameter in full.

        The pipeline checks the step's name and products before anything is registered,
        and a step that is refused is not added.

        Parameters
        ----------
        target
            A function returning a pandas DataFrame, defined at the top level of an
            importable module. Each input reaches it, under its parameter name, as the
            input's file path.
        inputs
            Maps each of ``target``'s parameter names to the file key of a product
            of a step already added, or of a root input.
        produces
            The file key of the step's product. Its suffix, ``.parquet`` or
            ``.csv``, names the format the returned DataFrame is saved in.
        resources
            Compute resources for this step in a run through Jobmon, such as
            ``{"memory": 16, "runtime": 7200}``. They override the pipeline's
            ``resources`` key by key.
        name
            The step's name. Defaults to ``produces``.

        Raises
        ------
        ValueError
            If the step's name is the name of a step already added; the message starts
            with ``Step '<name>':`` and says to give each step a different ``name=``.
            Also for each case in which
            :func:`~vivarium.dagger.frontend.python_step.python_step` raises it.
        vivarium.dagger.backend.wiring.WiringError
            If ``produces`` is a product of a step already added; the message starts
            with ``Step '<name>':`` and names the key and the step that produces it.
            If the step's name and one of its products are both already in the
            pipeline, this error is raised, not the ``ValueError`` for the name.
            Also for each case in which
            :func:`~vivarium.dagger.frontend.python_step.python_step` raises it, such as
            an input key that no step already added produces and that is not a root
            input.
        """
        products = [produces]
        self._check_new(produces if name is None else name, products)
        task = compile_python_step(
            target,
            inputs=inputs,
            produces=produces,
            registry=self._registry,
            resources=resources,
            name=name,
        )
        self._add(task, products)

    def add_notebook_step(
        self,
        notebook: Path | str,
        *,
        executed: FileKey,
        env: str,
        inputs: Mapping[str, FileKey] | None = None,
        produces: Mapping[str, FileKey] | None = None,
        parameters: Mapping[str, NotebookParameter] | None = None,
        resources: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> None:
        """Add a step that runs ``notebook`` with papermill in the environment ``env``.

        The step is compiled by
        :func:`~vivarium.dagger.frontend.notebook_step.notebook_step` with the pipeline's
        registry, which describes each parameter, the notebook's ``parameters`` cell and
        the failure message in full.

        The pipeline checks the step's name and products before anything is registered,
        and a step that is refused is not added.

        Parameters
        ----------
        notebook
            Path to the source notebook, an existing ``.ipynb`` file. Every name passed
            in ``inputs``, ``produces`` or ``parameters`` must be assigned in its first
            cell tagged ``parameters``.
        executed
            The file key, ending in ``.ipynb``, of the executed notebook, which is a
            product of the step.
        env
            The environment the notebook runs in: a conda env or venv name, or a path to
            its prefix. It must have ``papermill`` and ``ipykernel`` installed.
        inputs
            Maps notebook parameter names to the file keys of products of steps
            already added, or of root inputs. Each reaches the notebook as its path, as
            a ``str``.
        produces
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
            Also for each case in which
            :func:`~vivarium.dagger.frontend.notebook_step.notebook_step` raises it.
        vivarium.dagger.backend.wiring.WiringError
            If ``executed`` or a ``produces`` key is a product of a step already added;
            the message starts with ``Step '<name>':`` and names the key and the step
            that produces it. If the step's name and one of its products are both
            already in the pipeline, this error is raised, not the ``ValueError`` for
            the name. Also for each case in which
            :func:`~vivarium.dagger.frontend.notebook_step.notebook_step` raises it.
        """
        products = [executed, *(produces or {}).values()]
        self._check_new(executed if name is None else name, products)
        task = compile_notebook_step(
            notebook,
            executed=executed,
            registry=self._registry,
            env=env,
            inputs=inputs,
            produces=produces,
            parameters=parameters,
            resources=resources,
            name=name,
        )
        self._add(task, products)

    def add_command_step(
        self,
        command: str,
        *,
        produces: Mapping[str, FileKey],
        inputs: Mapping[str, FileKey] | None = None,
        env: str | None = None,
        resources: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> None:
        """Add a step that runs the shell ``command`` under ``/bin/sh``.

        The step is compiled by
        :func:`~vivarium.dagger.frontend.command_step.command_step` with the pipeline's
        registry, which describes the placeholders, the shell, ``env`` and the products
        in full.

        The pipeline checks the step's name and products before anything is registered,
        and a step that is refused is not added.

        Parameters
        ----------
        command
            The shell command, with a ``{name}`` placeholder for each input or product
            path it uses. Write ``{{`` and ``}}`` for a literal brace.
        produces
            Maps placeholder names to the file keys of the files the command writes.
            It must have at least one entry.
        inputs
            Maps placeholder names to the file keys of products of steps already
            added, or of root inputs.
        env
            The environment whose ``bin`` directories go first on ``PATH`` while the
            command runs: a conda env or venv name, or a path to its prefix. ``None``
            keeps the ``PATH`` of the process that runs the command.
        resources
            Compute resources for this step in a run through Jobmon. They override the
            pipeline's ``resources`` key by key.
        name
            The step's name. Defaults to the ``produces`` key when ``produces`` has one
            entry, and is required when it has more than one.

        Raises
        ------
        ValueError
            If the step's name is the name of a step already added; the message starts
            with ``Step '<name>':`` and says to give each step a different ``name=``.
            Also for each case in which
            :func:`~vivarium.dagger.frontend.command_step.command_step` raises it.
        vivarium.dagger.backend.wiring.WiringError
            If a ``produces`` key is a product of a step already added; the message
            starts with ``Step '<name>':`` and names the key and the step that produces
            it. If the step's name and one of its products are both already in the
            pipeline, this error is raised, not the ``ValueError`` for the name. Also
            for each case in which
            :func:`~vivarium.dagger.frontend.command_step.command_step` raises it.
        """
        products = list(produces.values())
        # With no name and other than one product, command_step has no default name and
        # raises before registering anything, so it reports the step.
        default = products[0] if len(products) == 1 else None
        self._check_new(default if name is None else name, products)
        task = compile_command_step(
            command,
            produces=produces,
            registry=self._registry,
            inputs=inputs,
            env=env,
            resources=resources,
            name=name,
        )
        self._add(task, products)

    def add_simulation_step(
        self,
        *,
        spec: Path | str,
        branches: Path | str,
        produces: FileKey,
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

        The pipeline checks the step's name and products before anything is registered,
        and a step that is refused is not added.

        Parameters
        ----------
        spec
            Path to the model specification, an existing file.
        branches
            Path to the branches file, an existing file.
        produces
            The file key of the run directory, the step's product. A step that names
            this key as an input receives the run directory's path.
        env
            The environment psimulate runs in: a conda env or venv name, or a path to
            its prefix. It must have ``vivarium-psimulate`` installed with its
            ``cluster`` extra.
        options
            The options of ``psimulate run``, keyed by long option name with ``_`` for
            ``-``, such as ``peak_memory``. ``project`` is required.
        artifact
            The file key of the model's artifact, a product of a step already added
            or a root input. Without it, psimulate reads the artifact the model
            specification names.
        resources
            Compute resources for this step in a run through Jobmon. They must cover the
            whole simulation, and they override the pipeline's ``resources`` key by key.
        name
            The step's name. Defaults to ``produces``.

        Raises
        ------
        ValueError
            If the step's name is the name of a step already added; the message starts
            with ``Step '<name>':`` and says to give each step a different ``name=``.
            Also for each case in which
            :func:`~vivarium.dagger.frontend.simulation_step.simulation_step` raises it.
        vivarium.dagger.backend.wiring.WiringError
            If ``produces`` is a product of a step already added; the message starts
            with ``Step '<name>':`` and names the key and the step that produces it.
            If the step's name and one of its products are both already in the
            pipeline, this error is raised, not the ``ValueError`` for the name.
            Also for each case in which
            :func:`~vivarium.dagger.frontend.simulation_step.simulation_step` raises it.
        """
        products = [produces]
        self._check_new(produces if name is None else name, products)
        task = compile_simulation_step(
            spec=spec,
            branches=branches,
            produces=produces,
            registry=self._registry,
            env=env,
            options=options,
            artifact=artifact,
            resources=resources,
            name=name,
        )
        self._add(task, products)

    def add_task(self, task: Task) -> None:
        """Add ``task``, a step that is already compiled, to the end of the pipeline.

        This is the lower layer under the step methods, for a task compiled with a step
        function directly, such as
        :func:`~vivarium.dagger.frontend.python_step.python_step`, or built by hand as a
        :class:`~vivarium.dagger.backend.task.Task`. The pipeline's registry is private
        and knows only the root inputs and the products of steps added with the step
        methods. So :func:`~vivarium.dagger.frontend.execution.preview` names by path
        each output of such a task and each input whose key the pipeline does not know,
        and a step added with a step method cannot name one of its outputs as an input.

        The pipeline checks the task before anything changes, and a task that is refused
        is not added. Each output that is a file path node is compared, by its resolved
        path, with the outputs of the steps already added; any other output is not. The
        ``dagger`` command refuses a pipeline in which two steps have an output that is
        not a path node with the same node name.

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
        for path in paths:
            self._task_outputs[path] = task.name

    def _check_new(self, step: str | None, products: Iterable[FileKey]) -> None:
        """Refuse a step whose name or one of whose products an added step already has."""
        # A missing or empty name is the step function's error to report.
        if not step:
            return
        # Products first: two unnamed steps that produce one key also share a default
        # name, and a different name= would not fix that.
        for key in products:
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

    def _add(self, task: Task, products: Iterable[FileKey]) -> None:
        """Append ``task`` and record it as the producer of each of ``products``."""
        self._tasks.append(task)
        for key in products:
            self._producers[key] = task.name


@dataclass(frozen=True)
class _PipelineSnapshot:
    """Hold the fixed tasks, state directory, registry and resources that one run reads."""

    tasks: Sequence[Task]
    # The absolute directory for pytask's configuration, state database and lockfile.
    root: Path
    # Without a registry, preview names files by path.
    registry: FileRegistry | None = None
    resources: Mapping[str, Any] = field(default_factory=dict)

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
        :attr:`Pipeline.state`, whose ``registry`` is the pipeline's registry, and whose
        ``resources`` are a copy of :attr:`Pipeline.resources`.

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
    # Normalize lexically, not with resolve(), so a run directory that is a symlink to
    # another place is still accepted, as FileRegistry accepts it.
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
