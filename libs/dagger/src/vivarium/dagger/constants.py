"""Defaults that ``vivarium.dagger`` ships with.

A pipeline definition run through Jobmon takes each step's compute resources from four
layers, each of which overrides the one before it key by key:

1. :data:`DEFAULT_RESOURCES`, the baseline in this module.
2. The definition's own defaults,
   :attr:`Pipeline.resources <vivarium.dagger.frontend.pipeline.Pipeline.resources>`.
3. The ``dagger`` command's ``-P/--project`` and ``-q/--queue`` options, or the
   ``jobmon_resources`` keyword argument of :func:`vivarium.dagger.frontend.pipeline.run`.
4. The step's own ``resources=``, kept on
   :attr:`Task.resources <vivarium.dagger.backend.task.Task.resources>`.

A model keeps its own defaults in its own constants module and passes them to its
``Pipeline``.

This module imports only the standard library.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

DEFAULT_RESOURCES: Mapping[str, Any] = MappingProxyType(
    {"queue": "all.q", "memory": 1, "runtime": 3600, "cores": 1}
)
"""The compute resources that every step of a pipeline definition run through Jobmon
starts from, in the units that Jobmon's Slurm cluster takes: ``memory`` in gigabytes and
``runtime`` in seconds. It names no project, because the project depends on who runs the
pipeline. The mapping is read-only."""
