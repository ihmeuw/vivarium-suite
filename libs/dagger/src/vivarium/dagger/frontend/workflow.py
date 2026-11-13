"""Author a pipeline by adding steps to one workflow object.

A :class:`Workflow` holds a run's output root, its run name, the
:class:`~vivarium.dagger.backend.wiring.ArtifactRegistry` that wires its steps, the
directory for its pytask state, and the steps added so far. A pipeline definition makes
one workflow, adds each step with a method such as :meth:`Workflow.python_step`, and
ends with ``return wf.pipeline()``::

    def pipeline() -> Pipeline:
        wf = Workflow(HERE / "output", run_name="example")
        wf.python_step(make_population, inputs={}, produces="population.parquet")
        wf.python_step(
            total, inputs={"population": "population.parquet"}, produces="total.csv"
        )
        return wf.pipeline()

Each step method calls the step function of the same name, such as
:func:`~vivarium.dagger.frontend.python_step.python_step`, with the workflow's registry,
so a step added through the workflow is the same task, with the same checks and
messages, as one compiled against the registry by hand. The workflow also refuses a step
whose name, or one of whose products, another step in the workflow already has, when the
step is added; when both apply, the error names the product. A step that reads an
artifact must still be added after the step that produces it.

By default the workflow keeps its pytask state in ``<output root>/.dagger/<run name>``,
outside the run's output directory ``<output root>/<run name>``. Two workflows with
different run names under one output root therefore write their products to different
directories and keep separate state, so they do not share a lock.
"""

import os
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import ArtifactKey, ArtifactRegistry, WiringError
from vivarium.dagger.frontend.command_step import command_step as compile_command_step
from vivarium.dagger.frontend.notebook_step import NotebookParameter
from vivarium.dagger.frontend.notebook_step import notebook_step as compile_notebook_step
from vivarium.dagger.frontend.pipeline import Pipeline
from vivarium.dagger.frontend.python_step import python_step as compile_python_step
from vivarium.dagger.frontend.simulation_step import (
    simulation_step as compile_simulation_step,
)

STATE_DIR = ".dagger"
"""The directory under the output root that holds each run's default state directory."""


class Workflow:
    """Collect the steps of one run and compile them into a pipeline.

    Parameters
    ----------
    output_root
        Directory under which the run's products live, in ``<output root>/<run name>``.
        A relative path is resolved against the current directory when the workflow is
        made.
    run_name
        Names the run's output directory and, by default, its state directory, so two
        runs under one output root do not collide.
    root_inputs
        Source files the pipeline reads but does not produce, keyed by the artifact key
        a step names as an input, as for
        :class:`~vivarium.dagger.backend.wiring.ArtifactRegistry`. The mapping that
        :func:`~vivarium.dagger.frontend.handoff.handoff_inputs` returns can be passed
        here.
    state
        Absolute directory for the run's pytask configuration, state database, lockfile
        and run lock, which becomes :attr:`Pipeline.root
        <vivarium.dagger.frontend.pipeline.Pipeline.root>`. Defaults to
        ``<output root>/.dagger/<run name>``, with the output root resolved.
    resources
        The definition's default compute resources for a run through Jobmon, which
        become :attr:`Pipeline.resources
        <vivarium.dagger.frontend.pipeline.Pipeline.resources>`. Defaults to none.

    Raises
    ------
    ValueError
        If the run's output directory, ``<output root>/<run name>`` with ``.`` and ``..``
        collapsed (symlinks are not followed), is not strictly inside the output root
        (as for an empty run name, ``.``, ``..`` or an absolute path), or is
        :data:`STATE_DIR` or a directory inside it (as for the run name ``.dagger``),
        since the run's products would then share a directory with the state of every
        run; or if ``state`` is a relative path, which would move the run's state with
        the current directory. The message names the run name or the state path.
    TypeError
        If ``resources`` is not a mapping.
    """

    def __init__(
        self,
        output_root: Path | str,
        run_name: str,
        root_inputs: Mapping[ArtifactKey, Path | str] | None = None,
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
                    "Workflow state must be an absolute path, got the relative path "
                    f"{str(state_dir)!r}."
                )
        if resources is not None and not isinstance(resources, Mapping):
            raise TypeError(
                "Workflow resources must be a mapping, got "
                f"{type(resources).__name__} {resources!r}."
            )
        self._output_root = root
        self._run_name = run_name
        self._state = state_dir
        self._registry = ArtifactRegistry(root, run_name, root_inputs)
        self._resources: dict[str, Any] = {} if resources is None else dict(resources)
        self._tasks: list[Task] = []
        self._producers: dict[ArtifactKey, str] = {}

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
    def registry(self) -> ArtifactRegistry:
        """The registry that wires the workflow's steps, for the run's output directory."""
        return self._registry

    @property
    def resources(self) -> Mapping[str, Any]:
        """A copy of the definition's default compute resources, empty if none were given."""
        return dict(self._resources)

    def python_step(
        self,
        target: Callable[..., pd.DataFrame],
        *,
        inputs: Mapping[str, ArtifactKey],
        produces: ArtifactKey,
        resources: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> None:
        """Add a step that calls ``target`` and saves the DataFrame it returns.

        The step runs in the pipeline's own environment. It is compiled by
        :func:`~vivarium.dagger.frontend.python_step.python_step` with the workflow's
        registry, which describes each parameter in full.

        The workflow checks the step's name and products before anything is registered,
        and a step that is refused is not added.

        Parameters
        ----------
        target
            A function returning a pandas DataFrame, defined at the top level of an
            importable module. Each input reaches it, under its parameter name, as the
            input's file path.
        inputs
            Maps each of ``target``'s parameter names to the artifact key of a product
            of a step already added, or of a root input.
        produces
            The artifact key of the step's product. Its suffix, ``.parquet`` or
            ``.csv``, names the format the returned DataFrame is saved in.
        resources
            Compute resources for this step in a run through Jobmon, such as
            ``{"memory": 16, "runtime": 7200}``. They override the workflow's
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
            workflow, this error is raised, not the ``ValueError`` for the name.
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

    def notebook_step(
        self,
        notebook: Path | str,
        *,
        executed: ArtifactKey,
        env: str,
        inputs: Mapping[str, ArtifactKey] | None = None,
        produces: Mapping[str, ArtifactKey] | None = None,
        parameters: Mapping[str, NotebookParameter] | None = None,
        resources: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> None:
        """Add a step that runs ``notebook`` with papermill in the environment ``env``.

        The step is compiled by
        :func:`~vivarium.dagger.frontend.notebook_step.notebook_step` with the workflow's
        registry, which describes each parameter, the notebook's ``parameters`` cell and
        the failure message in full.

        The workflow checks the step's name and products before anything is registered,
        and a step that is refused is not added.

        Parameters
        ----------
        notebook
            Path to the source notebook, an existing ``.ipynb`` file. Every name passed
            in ``inputs``, ``produces`` or ``parameters`` must be assigned in its first
            cell tagged ``parameters``.
        executed
            The artifact key, ending in ``.ipynb``, of the executed notebook, which is a
            product of the step.
        env
            The environment the notebook runs in: a conda env or venv name, or a path to
            its prefix. It must have ``papermill`` and ``ipykernel`` installed.
        inputs
            Maps notebook parameter names to the artifact keys of products of steps
            already added, or of root inputs. Each reaches the notebook as its path, as
            a ``str``.
        produces
            Maps notebook parameter names to the artifact keys of the data files the
            notebook writes. Each reaches the notebook as its path, as a ``str``.
        parameters
            Maps notebook parameter names to scalar values: a ``str``, a real number, a
            ``bool`` or ``None``.
        resources
            Compute resources for this step in a run through Jobmon. They override the
            workflow's ``resources`` key by key.
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
            already in the workflow, this error is raised, not the ``ValueError`` for
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

    def command_step(
        self,
        command: str,
        *,
        produces: Mapping[str, ArtifactKey],
        inputs: Mapping[str, ArtifactKey] | None = None,
        env: str | None = None,
        resources: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> None:
        """Add a step that runs the shell ``command`` under ``/bin/sh``.

        The step is compiled by
        :func:`~vivarium.dagger.frontend.command_step.command_step` with the workflow's
        registry, which describes the placeholders, the shell, ``env`` and the products
        in full.

        The workflow checks the step's name and products before anything is registered,
        and a step that is refused is not added.

        Parameters
        ----------
        command
            The shell command, with a ``{name}`` placeholder for each input or product
            path it uses. Write ``{{`` and ``}}`` for a literal brace.
        produces
            Maps placeholder names to the artifact keys of the files the command writes.
            It must have at least one entry.
        inputs
            Maps placeholder names to the artifact keys of products of steps already
            added, or of root inputs.
        env
            The environment whose ``bin`` directories go first on ``PATH`` while the
            command runs: a conda env or venv name, or a path to its prefix. ``None``
            keeps the ``PATH`` of the process that runs the command.
        resources
            Compute resources for this step in a run through Jobmon. They override the
            workflow's ``resources`` key by key.
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
            workflow, this error is raised, not the ``ValueError`` for the name. Also
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

    def simulation_step(
        self,
        *,
        spec: Path | str,
        branches: Path | str,
        produces: ArtifactKey,
        env: str,
        options: Mapping[str, str | int | float | bool],
        artifact: ArtifactKey | None = None,
        resources: dict[str, Any] | None = None,
        name: str | None = None,
    ) -> None:
        """Add a step that runs a vivarium simulation with ``psimulate run``.

        The step is compiled by
        :func:`~vivarium.dagger.frontend.simulation_step.simulation_step` with the
        workflow's registry, which describes the command, the run directory, the options
        and the failure message in full. psimulate runs only on a cluster compute node.

        The workflow checks the step's name and products before anything is registered,
        and a step that is refused is not added.

        Parameters
        ----------
        spec
            Path to the model specification, an existing file.
        branches
            Path to the branches file, an existing file.
        produces
            The artifact key of the run directory, the step's product. A step that names
            this key as an input receives the run directory's path.
        env
            The environment psimulate runs in: a conda env or venv name, or a path to
            its prefix. It must have ``vivarium-psimulate`` installed with its
            ``cluster`` extra.
        options
            The options of ``psimulate run``, keyed by long option name with ``_`` for
            ``-``, such as ``peak_memory``. ``project`` is required.
        artifact
            The artifact key of the model's artifact, a product of a step already added
            or a root input. Without it, psimulate reads the artifact the model
            specification names.
        resources
            Compute resources for this step in a run through Jobmon. They must cover the
            whole simulation, and they override the workflow's ``resources`` key by key.
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
            workflow, this error is raised, not the ``ValueError`` for the name.
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

    def pipeline(self) -> Pipeline:
        """Return a frozen pipeline of the steps added so far.

        Each call returns a new
        :class:`~vivarium.dagger.frontend.pipeline.Pipeline`. A step added after a call
        does not reach the pipeline that call returned.

        Returns
        -------
            A :class:`~vivarium.dagger.frontend.pipeline.Pipeline` whose ``tasks`` are
            the tasks of the steps added so far, as a tuple in the order they were
            added, whose ``root`` is :attr:`state`, whose ``registry`` is
            :attr:`registry`, and whose ``resources`` are a copy of :attr:`resources`.
        """
        return Pipeline(
            tuple(self._tasks),
            root=self._state,
            registry=self._registry,
            resources=dict(self._resources),
        )

    def _check_new(self, step: str | None, products: Iterable[ArtifactKey]) -> None:
        """Refuse a step whose name or one of whose products an added step already has."""
        # A missing or empty name is the step function's error to report.
        if not step:
            return
        # Products first: two unnamed steps that produce one key also share a default
        # name, and a different name= would not fix that.
        for key in products:
            producer = self._producers.get(key)
            if producer is not None:
                raise WiringError(
                    f"Step {step!r}: output {key!r} is already produced by step "
                    f"{producer!r}; each artifact key can have only one producing step."
                )
        if any(task.name == step for task in self._tasks):
            raise ValueError(
                f"Step {step!r}: a step named {step!r} was already added to the workflow; "
                "give each step a different name= to tell them apart."
            )

    def _add(self, task: Task, products: Iterable[ArtifactKey]) -> None:
        """Append ``task`` and record it as the producer of each of ``products``."""
        self._tasks.append(task)
        for key in products:
            self._producers[key] = task.name


def _check_run_name(root: Path, run_name: str) -> None:
    """Refuse a run name whose output directory is not its own directory under ``root``."""
    # Normalize lexically, not with resolve(), so a run directory that is a symlink to
    # another place is still accepted, as ArtifactRegistry accepts it.
    if Path(run_name).is_absolute():
        # An absolute name inside the root would make the default state, built from the
        # run name, a directory inside the run's own output directory.
        raise ValueError(
            f"Workflow run name {run_name!r} is an absolute path. Give a relative run "
            "name, such as 'run1', that names a directory under the output root "
            f"{root}."
        )
    run_dir = Path(os.path.normpath(root / run_name))
    state_root = root / STATE_DIR
    if root not in run_dir.parents:
        raise ValueError(
            f"Workflow run name {run_name!r} puts the run's output directory at {run_dir}, "
            f"which is not inside the output root {root}. Give a relative run name, such "
            "as 'run1', that names a directory under the output root."
        )
    if run_dir == state_root or state_root in run_dir.parents:
        raise ValueError(
            f"Workflow run name {run_name!r} puts the run's output directory at {run_dir}, "
            f"which is {STATE_DIR!r} or inside it; that directory holds the state of every "
            f"run under the output root {root}. Give a run name outside {STATE_DIR!r}, such "
            "as 'run1'."
        )
