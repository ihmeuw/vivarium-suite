.. _troubleshooting:

===============
Troubleshooting
===============

This page covers problems that people meet when they run a pipeline definition with
``dagger``, most of them in a run through Jobmon. Each section gives what you see, why
it happens, and what to do. :doc:`python_pipelines` describes the commands. This
version is an alpha: every step that a step method adds runs on every ``dagger run``
and ``dagger force``, and after a failure or an interruption the next run runs every
such step again. :ref:`what_the_alpha_does` says what this means. Long
messages below are wrapped to fit the page; ``dagger`` prints each one on one line.

.. contents::
   :local:
   :depth: 1

The run stopped when the allocation ended or the session closed
---------------------------------------------------------------

In a run through Jobmon, the ``dagger`` process plans the run, submits the steps as one
Jobmon workflow, and then waits until the whole workflow ends. It records the steps'
results in the pipeline's state only when the wait ends.

In a Slurm allocation whose end time Slurm gives in ``SLURM_JOB_END_TIME``, as it does
in batch jobs and ``srun`` sessions, ``dagger`` by default lets Jobmon run for the time
left in the allocation less 300 seconds. When that time runs out, Jobmon stops, and
``dagger`` records the steps that finished, reports each step that was running or
waiting as below, lists the steps after them under ``Not run because an upstream step
failed:``, and exits with status 1. After Jobmon's time limit both statuses in this
message read ``'unknown'``:

.. code-block:: text

   Step 'adults' has no result because Jobmon stopped the workflow with an error,
   RuntimeError: Not all tasks completed within the given workflow timeout length
   (<N> seconds). Submitted tasks will still run, but the workflow will need to be
   restarted. It was not recorded and will run again in the next build. Its Jobmon
   task status is '<status>'. Jobmon workflow <id> has the workflow status '<status>'.

If Jobmon has not returned when half of that margin is left, ``dagger`` stops waiting by
itself: it prints the line below and then stops as for SIGTERM. Without
``SLURM_JOB_END_TIME``, the time limit comes from ``squeue`` less 120 seconds, and there
is no such stop.

.. code-block:: text

   Jobmon's run had not returned, and this Slurm allocation ends at <date and time>,
   so the bridge stopped waiting for the workflow and settles the run.

If the ``dagger`` process gets SIGTERM or SIGHUP while it submits or waits for the
workflow, it records the steps that finished,
prints the message below, and exits with status 130. Ctrl-C makes Jobmon ask whether to
stop, and if you confirm, the result is the same.

.. code-block:: text

   Interrupted. The Jobmon workflow <id> was not cancelled; its jobs that already
   started may still be running. Run the same command again to run the pipeline
   again.

If the process is killed outright, it prints nothing and records nothing.

Which of these happens depends on how you started ``dagger``. At the time limit, Slurm
sends SIGTERM only to the job's own process, and kills it 30 seconds later. ``dagger``
is the job's own process when a batch script starts it with ``exec``, or when you start
it with ``exec`` in an interactive ``srun --pty bash`` shell. A ``dagger`` command that
you type into that shell without ``exec`` gets no signal and is killed 30 seconds after
the limit, so only the 300-second margin and the stop at half of it let it record
results. If the connection of an interactive ``srun --pty`` session drops, Slurm kills
``dagger`` at once, with no signal.

In the alpha, after any of these stops the next run runs every step again, including
the steps that finished. ``dagger`` does not cancel the workflow,
so its jobs that already started keep running and writing outputs. The next run warns
about them:

.. code-block:: text

   The latest earlier run under <root>, Jobmon workflow <id> in <run directory>, did
   not finish: its state is 'interrupted'. Its jobs on compute nodes may still be
   running and writing the same outputs as this build.

After Jobmon's time limit, the warning says ``stopped with an error (...)`` instead,
and after the process was killed it gives the state ``'running'``.

To avoid a stop, and to recover from one:

- Start ``dagger`` in an allocation that lasts longer than the whole workflow,
  including the time its steps wait in the Slurm queue, plus five minutes.
- Run a long build in a batch job, and start ``dagger`` there with ``exec``, as
  :doc:`python_pipelines` shows under "Running through Jobmon".
- For an interactive run, start ``tmux`` or ``screen`` on the login node and run
  ``srun`` inside it, so that a dropped connection to the login node does not end the
  session. In the ``srun`` shell, start ``dagger`` with ``exec``. The session then ends
  when ``dagger`` ends.
- From Python, :func:`~vivarium.dagger.frontend.execution.run` takes
  ``jobmon_settle_margin=`` to change the 300 seconds, and ``jobmon_timeout=`` to give
  Jobmon a fixed number of seconds, for example
  ``run(pipeline, jobmon=True, jobmon_settle_margin=600)``. The ``dagger`` command has
  no option for either.
- Before you run again, cancel the old workflow's jobs, so that they do not write the
  same outputs as the new run. ``jobmon workflow tasks -w <id> -l 100`` lists the
  workflow's tasks with their statuses. Each task is named after its step, with ``/``,
  ``\``, quotes and spaces written as ``_``, so ``sims/toy`` is ``sims_toy``.
  ``squeue -u $USER`` lists your Slurm jobs, and ``scancel <job id>`` cancels one.

If the allocation has no more than the margin left when the run starts, ``dagger``
submits nothing and says:

.. code-block:: text

   This Slurm allocation ends in <s> seconds (SLURM_JOB_END_TIME), which is not more
   than jobmon_settle_margin, <m> seconds, so Jobmon would have no time to run the
   workflow. Start the build in a longer allocation or set a smaller
   jobmon_settle_margin.

Steps fail with no result when the pipeline's root is on /tmp
--------------------------------------------------------------

The steps that run first fail, ``dagger`` lists every other step after ``Not run because
an upstream step failed:``, and it exits with status 1. pytask's report for each failed
step shows no output from the step, only this:

.. code-block:: text

   Step 'population' has no result because the workflow ended without a result for
   the step, as when the step's runner is killed on its node. It was not recorded and
   will run again in the next build. Its Jobmon task status is '<status>'. Jobmon
   workflow <id> has the workflow status '<status>'.

The pipeline's root, ``<output root>/.dagger/<run name>`` unless the ``Pipeline`` sets
``state=``, holds the run directory where ``dagger`` writes each step's recipe and where
the step writes its result. Each step runs on a compute node, which cannot see the
``/tmp`` of the host that runs ``dagger``. When the step's runner does not find the run
directory, it runs nothing and writes the message below to the standard error of its
Slurm job. Jobmon keeps the end of that stream with the failed attempt, so you see the
message in Jobmon's GUI and not in ``dagger``'s output. Jobmon tries the step again up
to the ``-m/--max-attempts`` limit before it gives up.

.. code-block:: text

   vivarium.dagger.bridge.runner: the result's directory <dir> does not exist on
   <host>. A bridged build's state directory must be on a filesystem that every node
   can see, not on a node's own disk such as /tmp.

``dagger`` checks only that it can write the root, so it does not refuse ``/tmp``
before it submits the steps.

Put the ``Pipeline``'s output root, or its ``state=``, on storage that every compute
node can read and write, such as a team share. The inputs, and the directory of the
definition's modules, must be readable from every node too (see "Running through
Jobmon" in :doc:`python_pipelines`). Then run again. The state left under ``/tmp`` is of
no use, and you can delete it. A run with ``--local`` runs every step in the ``dagger``
process, so it works with a root on ``/tmp``.

A run through Jobmon from an overlay venv fails at once
-------------------------------------------------------

An overlay venv is a venv made with ``--system-site-packages`` on top of a shared conda
environment, so that it uses the shared environment's packages. A run through Jobmon
from such a venv fails at once: the steps that run first fail, and the others do not
run.

The ``dagger`` process submits each step through Jobmon's Slurm plugin, and the plugin
starts each Slurm job with Jobmon's ``worker_node_entry_point`` script. The plugin looks
for that script on ``PATH``, and some Jobmon versions also look beside the running
``python``. In an overlay venv the script is only in the shared environment's ``bin``
directory, which is on neither. The message that you see depends on the Jobmon version.
``dagger`` puts the shared environment's ``bin`` directory on ``PATH`` for each step's
own command on the node, but that does not help here, because the plugin looks for the
script in the ``dagger`` process, before any step runs.

Put the shared environment's ``bin`` directory on ``PATH`` before you start ``dagger``,
or activate the shared environment before the venv. ``sys.base_prefix`` is the shared
environment's prefix:

.. code-block:: console

   $ export PATH="$PATH:$(python -c 'import sys; print(sys.base_prefix)')/bin"

From Python, do the same before you call
:func:`~vivarium.dagger.frontend.execution.run`:

.. code-block:: python

   import os
   import sys
   from pathlib import Path

   os.environ["PATH"] += os.pathsep + str(Path(sys.base_prefix) / "bin")

The Jobmon bridge's plugin is not registered
--------------------------------------------

A run through Jobmon stops before it writes anything, and ``dagger`` exits with
status 2:

.. code-block:: text

   Error: jobmon=True needs the Jobmon bridge, but its pytask plugin is not registered
   in this environment: no 'pytask' entry point names vivarium.dagger.bridge.plugin,
   so every step would run in this process. Reinstall vivarium-dagger; for an editable
   install, run `pip install -e` on libs/dagger again, because one made before the
   bridge existed lacks the entry point.

From Python, :func:`~vivarium.dagger.frontend.execution.run` raises
:class:`~vivarium.dagger.frontend.execution.PipelineError` with the same message.

pytask finds the bridge only through an entry point in vivarium-dagger's installed
metadata. An editable install made before the bridge existed has no such entry point,
and without the check, every step would run in the ``dagger`` process. To see whether
the entry point is there:

.. code-block:: console

   $ python -c "import importlib.metadata as m; print(m.entry_points(group='pytask'))"

The output must include ``value='vivarium.dagger.bridge.plugin'``. If it does not,
install vivarium-dagger again. For an editable install, run ``pip install -e
libs/dagger`` from the root of the vivarium-suite checkout. With uv, run ``uv pip
install -e libs/dagger --reinstall-package vivarium-dagger --refresh-package
vivarium-dagger``, because uv can otherwise reuse the old metadata from its cache.

Another run holds the lock on this pipeline record
--------------------------------------------------

A run through Jobmon, or a ``dagger force`` through Jobmon, stops before it plans
anything and exits with status 1:

.. code-block:: text

   Error: Another run holds the lock on this pipeline record (user jdoe, pid 4242 on
   host node-17 in Slurm job 123, since 2026-10-28T10:02:04-07:00). Lock file
   /path/to/output/.dagger/example/run.lock. Wait for that run to finish, then run
   again. To stop it instead, run "ssh node-17 kill 4242": kill sends SIGTERM, and a
   bridged run then records the steps that finished before it exits.

When the run that holds the lock is yours and on the same host, the message gives
``kill <pid>`` instead. When it is another user's, it ends with ``To stop it instead,
ask <user>, who started it.`` If that run has not yet written its line into the lock
file, the parentheses say ``holder unknown``; try again in a moment.

Only one run through Jobmon works on a pipeline's root at a time. The root is
``<output root>/.dagger/<run name>`` unless the ``Pipeline`` sets ``state=``. A run
through Jobmon holds a lock on the file ``run.lock`` in the root from before it plans
until it has recorded its results. The lock is released when that process ends, also
when it is killed, so a refusal means that the process is still alive. The file stays
after the lock is released and keeps the line of the last run that held it, so the file
alone does not show that the lock is held.

To find out whether that run is still working, use the host, pid and Slurm job in the
message:

.. code-block:: console

   $ ssh node-17 ps -p 4242
   $ squeue -j 123

Then wait for the run to finish, or stop it with the ``kill`` command that the message
gives. The run then stops as described in
`The run stopped when the allocation ended or the session closed`_: it exits with status
130, and its workflow's jobs that already started keep running until you cancel them.

A run with ``--local`` and ``dagger preview`` take no lock. Do not start a ``--local``
run of a pipeline while a run through Jobmon works on it, because both write the
pipeline's state.

``dagger restart`` does not help with a stopped run of a pipeline definition. It
continues a YAML workflow from its output directory, and refuses a definition.

Steps waited in the Slurm queue until the run's time ran out
------------------------------------------------------------

The run ends with exit status 1, and each step that had not finished has the message
``Step '<name>' has no result because Jobmon stopped the workflow with an error,
RuntimeError: Not all tasks completed within the given workflow timeout length (<N>
seconds)...``, the same message as in
`The run stopped when the allocation ended or the session closed`_. ``squeue`` showed
some of the steps' jobs as ``PENDING``, waiting for a node, for most of the run.

``dagger`` gives Jobmon one time limit for the whole workflow, and the time that steps
wait in the Slurm queue counts against it. The limit is:

- ``jobmon_timeout``, if a run from Python sets it;
- otherwise, in an allocation, the time left in the allocation less
  ``jobmon_settle_margin`` (300 seconds by default);
- otherwise Jobmon's own default of 36000 seconds.

Jobmon counts this time from when it starts to schedule the jobs, a few seconds after
``dagger`` works it out. A step's ``runtime`` resource is a different limit: it is the
time limit of that step's own Slurm job, counted from when the job starts.

As the message says, jobs that Jobmon already submitted keep running. ``dagger`` does
not cancel them, so a job that was waiting can start after the run ended and write
outputs that no run records.

What to do:

- Cancel the old workflow's jobs before you run again, as
  `The run stopped when the allocation ended or the session closed`_ describes.
- Start ``dagger`` in a longer allocation, so that the steps have time to wait in the
  queue and run.
- Send the steps to a less busy queue with ``-q/--queue``, or one step with its own
  ``resources={"queue": ...}``. ``-q`` sets the queue of the steps' jobs, not of the
  allocation that ``dagger`` runs in.
- From Python, pass ``jobmon_timeout=`` in seconds to
  :func:`~vivarium.dagger.frontend.execution.run` to set the limit yourself.

If the allocation's end time is not in the environment, ``dagger`` asks ``squeue`` for
the time left. If that fails, the run stops before it submits anything, with this
message; set ``jobmon_timeout`` from Python to avoid it:

.. code-block:: text

   The bridge could not work out how long Jobmon may run the workflow from the
   remaining time of this Slurm allocation (<error>). Set jobmon_timeout to the number
   of seconds that Jobmon may run the workflow.

A command step that writes an artifact fails on its second run
--------------------------------------------------------------

A command step that writes a vivarium artifact works on the first run and fails on the
next one:

.. code-block:: text

   Step 'artifact' failed: the command exited with status 1; its output is in pytask's
   report for the step.

The step's output in pytask's report ends with:

.. code-block:: text

   vivarium.artifact.artifact.ArtifactException: metadata.locations already in artifact.

``dagger`` does not remove a step's outputs before the step runs, so the command must
write every output on every run (see "Steps that run a command" in
:doc:`python_pipelines`). ``vivarium.artifact.Artifact`` opens an existing file and
keeps its keys, and its ``write`` refuses a key that the file already holds. The command
therefore finds the artifact from the earlier run and fails on the first key it writes
again. In the alpha a command step runs on every run, so this happens on the second run
of the pipeline. A step that failed partway, and Jobmon's next attempt at the same step,
also start from the half-written file.

Delete the artifact before you open it. In the script that the step runs:

.. code-block:: python

   import sys
   from pathlib import Path

   from vivarium.artifact import Artifact

   path = Path(sys.argv[1])
   path.unlink(missing_ok=True)
   artifact = Artifact(path)
   artifact.write("metadata.locations", ["Nigeria"])

Or delete it at the start of the step's command, for example
``rm -f {artifact} && python {script} {artifact}``.

Do not run vivarium-profiling's interactive ``make_artifacts`` in a step to replace an
existing artifact. It asks for confirmation before it deletes the artifact, and a step
cannot answer.

A run with --until runs more than the named step
------------------------------------------------

``--until STEP`` runs ``STEP`` and every step that it depends on, directly or through
other steps. In the alpha no step that a step method adds is ever current, so all of
those steps run, on every run. Give ``--until`` more than once to run what any of the
named steps needs. To see which steps a run would include before you start it:

.. code-block:: console

   $ dagger preview -c pipeline.py --until adults

Each step that ``--until`` leaves out shows as ``skip <step>: not selected``.

``dagger force STEP`` does not narrow a run: it runs the named steps together with every
other step, so it runs the same steps as ``dagger run``. Use ``dagger run --until`` to
run part of a pipeline.

In a run through Jobmon, every step of the pipeline needs a project, also the steps that
``--until`` leaves out.

A name that is not a step of the pipeline stops the command with status 2 before
anything runs. After ``dagger``'s usage line, the message reads:

.. code-block:: text

   Error: Invalid value for '--until': No step named 'adult'. Did you mean 'adults'?
   Valid steps: 'adults', 'population', 'summary'.

From Python, :func:`~vivarium.dagger.frontend.execution.run` and
:func:`~vivarium.dagger.frontend.execution.preview` take the same selection as
``only=``: a string names one step and a list names several, for example
``run(pipeline, only=["adults", "summary"])``. ``only=None``, the default, runs every
step, and an empty list runs no step.

A step failed on a compute node
-------------------------------

In a run through Jobmon, a step that fails on its node is reported in one of two ways.

When the step ran and failed, pytask's report for the step starts with
``Step '<name>' failed on its compute node.``, then gives the traceback from the node,
which starts in the step's own code, and the last 64 KiB of what the step printed.
``dagger``'s line for the step gives the error's type and message, or, for a command
step, the command's exit status, as described under "When a step fails" in
:doc:`python_pipelines`.

When the step left no result, the report says why, with the step's Jobmon task status
and the workflow's id. ``dagger``'s line for the step usually starts with
``Step '<name>' failed: RemoteTaskFailed: Step '<name>' has no result because`` and
then gives the cause, which is one of these:

- ``Jobmon stopped the workflow with an error, <type>: <message>``, for example
  Jobmon's time limit (see
  `Steps waited in the Slurm queue until the run's time ran out`_).
- ``the build was interrupted``, or ``the build was interrupted before the Jobmon
  workflow was bound`` (see
  `The run stopped when the allocation ended or the session closed`_).
- ``the workflow ended without a result for the step, as when the step's runner is
  killed on its node``. Slurm kills a step that runs out of memory or time, and Jobmon
  tries it again, with more memory or runtime, up to the ``-m/--max-attempts``
  limit. If the root is on a node's own disk, this is the cause
  too (see `Steps fail with no result when the pipeline's root is on /tmp`_).

Each step's whole output, past the 64 KiB in the report and also up to the moment a
killed step stopped, is in the run directory, under the pipeline's root:

.. code-block:: text

   <root>/.pytask/jobmon/<YYYYmmdd-HHMMSS>-<hex>/
       state.json
       <signature>.payload.json
       <signature>.result.json
       <signature>.result.stdout
       <signature>.result.stderr

Each run through Jobmon makes a new run directory, and their names sort by time. Each
step's files are named by a signature that pytask gives it, and the step's recipe, the
``.payload.json`` file, holds the step's name. Each attempt at a step replaces its
``.stdout`` and ``.stderr`` files. ``state.json`` gives the run's Jobmon workflow id,
its status, the error that stopped Jobmon if there was one, and the steps that were not
recorded as successful. For example, to read the standard error of the step ``adults``
in the newest run:

.. code-block:: console

   $ runs=output/.dagger/example/.pytask/jobmon
   $ run=$runs/$(ls $runs | tail -n 1)
   $ cat $run/state.json
   $ grep -l '"task": "adults"' $run/*.payload.json
   $ cat $run/<signature>.result.stderr

With the workflow id, Jobmon shows each step as a task named after the step:
``jobmon workflow tasks -w <id> -l 100`` lists the tasks and their statuses, and
Jobmon's GUI shows each task's attempts. For a simulation step, ``dagger``'s line names
the directory of psimulate's worker logs, as described under "When a step fails" in
:doc:`python_pipelines`.
