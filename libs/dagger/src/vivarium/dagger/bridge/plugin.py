"""Register the Jobmon bridge with pytask.

pytask loads this module in every run through the ``vivarium_dagger_bridge`` entry
point, so it imports only pytask and the standard library. It does nothing unless a
build sets ``jobmon=True``. Such a build runs its stale steps through
:mod:`~vivarium.dagger.bridge.execute` as one Jobmon workflow, unless it is a dry run,
an explanation, or runs under the debugger or a trace (:data:`LOCAL_ONLY_MODES`), which
stay in the build's process.

:func:`~vivarium.dagger.backend.build.build` passes its extra keyword arguments to
pytask's configuration, so a build sets the bridge's keys there:

``jobmon``
    Run the stale steps through Jobmon. The default is ``False``.
``jobmon_cluster``
    The Jobmon cluster to run on, such as ``"slurm"`` or ``"multiprocess"``. The default
    is :data:`DEFAULT_CLUSTER`.
``jobmon_resources``
    The compute resources that every step starts from, such as ``{"project":
    "proj_simscience", "queue": "all.q", "memory": 4, "runtime": 3600, "cores": 1}``.
    A step's :attr:`~vivarium.dagger.backend.task.Task.resources` override them key by
    key. The default is an empty dict.
``jobmon_max_attempts``
    How many times Jobmon tries each step. The default is :data:`DEFAULT_MAX_ATTEMPTS`.
``jobmon_resource_scales``
    How much Jobmon raises each named resource after a step fails for lack of it, as a
    fraction, such as ``{"memory": 0.5}``. The default, ``None``, keeps Jobmon's own,
    which raises memory and runtime by half.
``jobmon_timeout``
    How many seconds Jobmon may run the workflow before it stops waiting, as a positive
    whole number. The default, ``None``, takes the remaining time of the Slurm allocation
    that the build runs in, less a safety buffer, as the ``dagger`` runner does, and
    outside an allocation keeps Jobmon's own default of 36000 seconds (10 hours).

For example::

    build(tasks, root, jobmon=True, jobmon_resources={"project": "proj_simscience",
          "queue": "all.q", "memory": 4, "runtime": 3600, "cores": 1})

The process that runs the build waits for the whole workflow. On Slurm it must run on a
compute node with ``/opt/slurm/bin`` on ``PATH``, because Jobmon's Slurm plugin calls
``scontrol``, ``squeue`` and ``scancel`` from that host. The bridge needs the ``cluster``
extra: ``pip install "vivarium-dagger[cluster]"``. If this plugin is not registered, as
after an editable install made before the bridge existed, a build that sets
``jobmon=True`` raises ``ValueError`` before it writes anything
(:func:`~vivarium.dagger.backend.build.check_bridge_registered`), so its steps never run
in the build's own process by mistake.

Only one bridged build (``jobmon=True``, outside the local-only modes) runs on a root at a
time. Such a build holds the run lock on ``<root>/run.lock`` (see
:mod:`~vivarium.dagger.backend.lock`) from before it plans until it has recorded and
settled its run. A second such build on the same root, from another process, fails at once
with pytask's ``ExitCode.FAILED`` and a message that names the run that holds the lock; it
does not wait, and it leaves that run's ``pytask.lock`` and its journal as they are.
``run.lock`` is created writable by its group, so a build by another account in that group
is refused in the same way. A refused build leaves the
:class:`~vivarium.dagger.backend.lock.RunLocked` in its configuration under
:data:`~vivarium.dagger.backend.lock.RUN_REFUSED_KEY`. A build without Jobmon, a preview,
and a build in one of the :data:`LOCAL_ONLY_MODES` take no lock, so they can run beside a
bridge build on the same root, and their writes to ``pytask.lock`` can then interleave
with its writes.

The build records the steps' results in pytask's lockfile only after the workflow stops.
When the build's process gets SIGTERM, as Slurm sends at the end of a job's time limit
before it kills the job, the build stops waiting for the workflow, records the steps
whose results are already written, reports every other step as failed or skipped, and
writes pytask's lockfile. The build then ends with pytask's summary and is not killed,
and it fails unless every step was recorded as successful. The bridge does not cancel
the workflow, so its jobs that are already on compute nodes can still be running and
writing products, and the next build prints a warning that names the run. The next
build runs every step that was not recorded as successful and every step after one,
and skips any other recorded step whose inputs and code identity are unchanged. With
the default code identity no step counts as unchanged, so every step runs again.
If the process is killed outright, for example once Slurm's grace period after SIGTERM
is over, no step is recorded and the next build runs every step again, including the
steps that had finished. Give that process more time than the whole workflow needs. The
build gets Slurm's SIGTERM only if it is the job's own process: in a batch script, start
it with ``exec`` (for example ``exec python build.py``). Without ``exec``, the script's
shell gets the SIGTERM and exits, Slurm then kills the build at once, and the build
records nothing, as if it had been killed outright.
Each run's state file, ``<root>/.pytask/jobmon/<run>/state.json``, gives the run's
Jobmon workflow id even then (see :mod:`~vivarium.dagger.bridge.state`), and the build
leaves the run's final state in its configuration under ``bridge_run``. While the
workflow runs, Jobmon's progress lines, such as ``Workflow 50.0% complete (1/2 tasks)``,
are printed on standard error, or above pytask's live table when standard output is a
terminal, and Jobmon's GUI shows each step.
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
    config.setdefault("jobmon_timeout", None)


@hookimpl(trylast=True)
def pytask_post_parse(config: dict[str, Any]) -> None:
    """Register :mod:`~vivarium.dagger.bridge.execute` for a build that uses Jobmon.

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
    from vivarium.dagger.bridge import execute

    config["pm"].register(execute)
