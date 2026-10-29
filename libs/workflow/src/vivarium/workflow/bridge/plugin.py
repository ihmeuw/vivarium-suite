"""Register the Jobmon bridge with pytask.

pytask loads this module in every run through the ``vivarium_workflow_bridge`` entry
point, so it imports only pytask and the standard library. It does nothing unless a
build sets ``jobmon=True``. Such a build runs its stale steps through
:mod:`~vivarium.workflow.bridge.execute` as one Jobmon workflow, unless it is a dry run,
an explanation, or runs under the debugger or a trace (:data:`LOCAL_ONLY_MODES`), which
stay in the build's process.

:func:`~vivarium.workflow.backend.build.build` passes its extra keyword arguments to
pytask's configuration, so a build sets the bridge's keys there:

``jobmon``
    Run the stale steps through Jobmon. The default is ``False``.
``jobmon_cluster``
    The Jobmon cluster to run on, such as ``"slurm"`` or ``"multiprocess"``. The default
    is :data:`DEFAULT_CLUSTER`.
``jobmon_resources``
    The compute resources that every step starts from, such as ``{"project":
    "proj_simscience", "queue": "all.q", "memory": 4, "runtime": 3600, "cores": 1}``.
    A step's :attr:`~vivarium.workflow.backend.task.Task.resources` override them key by
    key. The default is an empty dict.
``jobmon_max_attempts``
    How many times Jobmon tries each step. The default is :data:`DEFAULT_MAX_ATTEMPTS`.
``jobmon_resource_scales``
    How much Jobmon raises each named resource after a step fails for lack of it, as a
    fraction, such as ``{"memory": 0.5}``. The default, ``None``, keeps Jobmon's own,
    which raises memory and runtime by half.

For example::

    build(tasks, root, jobmon=True, jobmon_resources={"project": "proj_simscience",
          "queue": "all.q", "memory": 4, "runtime": 3600, "cores": 1})

The process that runs the build waits for the whole workflow. On Slurm it must run on a
compute node with ``/opt/slurm/bin`` on ``PATH``, because Jobmon's Slurm plugin calls
``scontrol``, ``squeue`` and ``scancel`` from that host. The bridge needs the ``cluster``
extra: ``pip install "vivarium-workflow[cluster]"``.

The build records the steps' results in pytask's lockfile only after the whole workflow
ends. If the build's process is killed before then, for example at its Slurm time limit,
no step is recorded and the next build runs every step again, including the steps that
had finished. Give that process more time than the whole workflow needs. Jobmon itself
stops waiting after its default timeout of 36000 seconds (10 hours), whatever that
process's time limit. A longer workflow therefore fails at 10 hours: the build records
the steps that had finished and then fails with Jobmon's error. While the workflow runs,
Jobmon's progress lines, such as ``Workflow 50% complete (1/2 tasks)``, are printed on
standard error, or above pytask's live table when standard output is a terminal, and
Jobmon's GUI shows each step.
"""

from typing import Any

from pytask import hookimpl

LOCAL_ONLY_MODES: tuple[str, ...] = ("dry_run", "explain", "pdb", "trace")
"""The configuration flags under which a build stays in its own process even with
``jobmon=True``."""

DEFAULT_CLUSTER = "slurm"
"""The Jobmon cluster that a build runs on when it does not set ``jobmon_cluster``."""

DEFAULT_MAX_ATTEMPTS = 2
"""How many times Jobmon tries each step when a build does not set
``jobmon_max_attempts``."""


@hookimpl
def pytask_parse_config(config: dict[str, Any]) -> None:
    """Set each of the bridge's configuration keys that the build did not set.

    The defaults are those listed in this module's description. A key that the build
    set keeps its value.

    Parameters
    ----------
    config
        pytask's configuration for the build.
    """
    config.setdefault("jobmon", False)
    config.setdefault("jobmon_cluster", DEFAULT_CLUSTER)
    config.setdefault("jobmon_resources", {})
    config.setdefault("jobmon_max_attempts", DEFAULT_MAX_ATTEMPTS)
    config.setdefault("jobmon_resource_scales", None)


@hookimpl(trylast=True)
def pytask_post_parse(config: dict[str, Any]) -> None:
    """Register :mod:`~vivarium.workflow.bridge.execute` for a build that uses Jobmon.

    The module is registered with the build's own plugin manager, ``config["pm"]``,
    only when ``config["jobmon"]`` is true and none of :data:`LOCAL_ONLY_MODES` is. It is
    imported only then, so a build without Jobmon does not import it.

    Parameters
    ----------
    config
        pytask's configuration for the build, after every plugin has parsed it.
    """
    if not config.get("jobmon") or any(config.get(mode) for mode in LOCAL_ONLY_MODES):
        return
    from vivarium.workflow.bridge import execute

    config["pm"].register(execute)
