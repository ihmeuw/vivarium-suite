"""The fixed set of compiled steps that one run or preview reads.

A :class:`PipelineSnapshot` is the lower layer under the
:class:`~vivarium.dagger.frontend.builder.Pipeline` that a definition builds:
:func:`~vivarium.dagger.frontend.execution.run` and
:func:`~vivarium.dagger.frontend.execution.preview` freeze a pipeline into one with
:func:`~vivarium.dagger.frontend.builder.freeze` before they read its steps, so a step
added to the pipeline later does not change a run that has started. Code that compiles
tasks with the step functions directly, such as the tests, can make a snapshot itself
and pass it to the verbs or return it from a definition.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vivarium.dagger.backend.task import Task
from vivarium.dagger.backend.wiring import FileRegistry


@dataclass(frozen=True)
class PipelineSnapshot:
    """Compiled tasks, the directory that holds their pytask state, and their registry.

    Raises
    ------
    TypeError
        If ``root`` is not a :class:`pathlib.Path`, for example a string, or
        ``resources`` is not a mapping.
    ValueError
        If ``root`` is a relative path, which would move the pipeline's state with the
        current directory.
    """

    tasks: Sequence[Task]
    """The compiled tasks, in order. Each task's name is a step name. A sequence passed
    to the constructor is stored as a tuple, so the snapshot does not change when the
    caller changes the list it passed."""
    root: Path
    """Absolute directory for pytask's configuration, state database, and lockfile, as
    :func:`~vivarium.dagger.backend.build.build` uses it."""
    registry: FileRegistry | None = None
    """The registry the steps were wired through. With it,
    :func:`~vivarium.dagger.frontend.execution.preview` names files by file key; without
    it, by path."""
    resources: Mapping[str, Any] = field(default_factory=dict)
    """The definition's default compute resources for a run through Jobmon, such as
    ``{"project": "proj_simscience", "memory": 4}``. They override
    :data:`~vivarium.dagger.constants.DEFAULT_RESOURCES` key by key, and the ``dagger``
    command's options and each step's own ``resources=`` override them in turn. A run in
    this process ignores them. The default is an empty mapping."""

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
