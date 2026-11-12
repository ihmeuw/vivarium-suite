.. _python_pipelines:

===========================
Running a Python pipeline
===========================

The ``dagger`` command runs a Python pipeline definition from the command line, as it
runs a workflow YAML file (see :doc:`dagger`). It can preview which steps a run would
execute and why, run every stale step or only the steps that a named step needs, and
force a step to run even when it is current. It is installed with ``vivarium-dagger``.

``-c/--config`` tells the two apart: a value ending in ``.yaml`` or ``.yml`` is a
workflow YAML file, and anything else is a pipeline definition. ``dagger preview`` and
``dagger force`` take only pipeline definitions, and ``dagger restart`` takes only the
output directory of a YAML workflow. The options of ``dagger run`` that override a YAML
file's settings are refused for a pipeline definition, and ``--until`` and ``--local``
are refused for a YAML file. Each refusal exits with status 2.

A pipeline definition cannot run through Jobmon yet, so ``dagger run`` and
``dagger force`` require ``--local``, which runs the steps in this process. Without it,
they exit with status 2 and say so.

.. contents::
   :local:
   :depth: 1

Pipeline definitions
--------------------

A pipeline definition is a Python file or module with a function that takes no
arguments and returns a :class:`~vivarium.dagger.frontend.pipeline.Pipeline`. The
function is named ``pipeline`` unless you name another one. Every command takes the
definition with ``-c``, in one of these forms::

    path/to/file.py
    path/to/file.py:function
    package.module
    package.module:function

``dagger`` imports the definition and calls the function. Loading a definition builds
nothing and writes no state, so the function should only compile steps: it should not
read large data or start work itself. A file is imported as a top-level module named
by its stem, with its directory added to the front of ``sys.path``. A definition inside
a package should be named by its dotted module name, because a file loaded by path
cannot use relative imports. Keep a file's name distinct from the modules it imports;
``dagger`` refuses a file whose stem is the name of a module that is already imported,
such as ``pytask.py``.

A file path works from any directory. A dotted module name must be importable where
``dagger`` runs: its package installed (for example with ``pip install -e .``) or its
directory on ``PYTHONPATH``. ``dagger`` does not add the current directory to
``sys.path``.

The pipeline's ``root`` is the directory that holds its pytask state. It must be an
absolute path, so that the state does not depend on the directory ``dagger`` is run from.
Building it from the definition file's own location, as the example below does, keeps
the state next to the definition.

This example has three steps in a chain. The ``population`` step writes a table,
``adults`` reads it, and ``summary`` reads the output of ``adults``. Everything a
definition needs, including the ``ArtifactRegistry`` that wires the steps together, is
imported from ``vivarium.dagger.frontend``:

.. literalinclude:: ../../tests/frontend/pipeline_example/pipeline.py
   :language: python
   :caption: pipeline.py

The commands below are run from the directory that holds ``pipeline.py``, in the order
shown. The library's tests run each of them on a copy of this file, so the page and the
code cannot drift apart.

Previewing a run
----------------

.. code-block:: console

   $ dagger preview -c pipeline.py

``dagger preview`` runs nothing and writes no product. It prints one line per step, in
the order pytask would run them. A step that would run is printed as ``run <step>``,
followed by pytask's reasons, separated by semicolons. A step that would not run is
printed as ``skip <step>``, followed by why: ``unchanged``, or ``not selected`` when
``--until`` leaves it out. A last line, such as ``2 of 3 steps would run.``, counts
the steps that would run. Each reason names an artifact by its key, a step's code as
``code of '<step>'``, and a step as ``step '<step>'``, never by a file path. For this
example, before its first run, the output starts like this::

    run  population: code of 'population' has no recorded state; step 'population' has no recorded state; artifact 'population.parquet' is missing
    run  adults: step 'population' would run first

``dagger preview`` also takes ``--until``, and plans only the steps that ``dagger run``
would run with the same option.

pytask's own output is not shown while ``dagger preview`` plans. If pytask stops the plan
for a reason other than the errors listed under `Errors in a definition or a command`_,
for example because a step's input file is missing, ``dagger`` writes pytask's output to
standard error, followed by a line naming pytask's exit code, and exits with status 1.

Running steps
-------------

.. code-block:: console

   $ dagger run -c pipeline.py --local --until adults

``--until STEP`` runs ``STEP`` and every step it depends on, and nothing else. In this
example it runs ``population`` and ``adults``, and ``summary.csv`` is not written. Give
``--until`` more than once to run the steps that any of the named steps need.

.. code-block:: console

   $ dagger run -c pipeline.py --local

Without ``--until``, ``dagger run`` runs every stale step. A step whose inputs and code
are unchanged since it last succeeded is skipped. Every ``python_step`` re-runs on
every build for now, because a step cannot yet declare a fingerprint of its code; until
it can, this run executes all three steps again.

``dagger run`` prints pytask's progress table while the steps run.

Forcing a step
--------------

.. code-block:: console

   $ dagger force -c pipeline.py --local population

``dagger force`` runs each named step even when it is current, together with every other
stale step. A step that has never run is run as usual. A step downstream of a forced
step runs again only if the forced step's output changed. Until a step can declare a
fingerprint of its code, every ``python_step`` is stale on every build, so in this
example ``adults`` and ``summary`` run as well.

Simulation steps
----------------

:func:`~vivarium.dagger.frontend.simulation_step.simulation_step` runs a vivarium
simulation with ``psimulate run`` in the environment named by ``env``, which needs
``vivarium-psimulate`` installed with its ``cluster`` extra. psimulate runs only on a
cluster compute node: it refuses to start on a submit host or outside the cluster. A
``dagger run --local`` of a pipeline with a simulation step must therefore be started
inside an allocation, for example:

.. code-block:: text

   srun -A proj_simscience -p i.q -c 2 --mem 8G -t 01:00:00 --pty bash
   dagger run -c pipeline.py --local

psimulate submits its own Jobmon workflow and waits for it to finish. The allocation
that the step runs in must last for the whole simulation, and so must the step's
``resources`` runtime once a definition can run through Jobmon. psimulate stops waiting
when the allocation's time runs out, and the step then fails because some simulations
did not finish.

``produces`` names the run directory. A step that reads the simulation's results names
the same key as an input and receives the run directory's path: a ``pathlib.Path`` in a
``python_step`` target, and a ``str`` in a notebook step. The run directory holds
psimulate's run, with one file per simulation for each metric in
``results/<metric>/*.parquet``, the simulations' metadata in ``metadata/``, and
psimulate's logs in ``logs/``. The step counts as done only when every simulation
finished and wrote every metric, so a consumer never reads a partial run. The registry's
output root must be on storage that every node sees, because psimulate's workers write
the results.

When a simulation does not finish, the step fails with how many of the simulations
finished, and the steps that read its results do not run. The last complete run stays
in the run directory, where the consumers expect it. The partial run stays beside it, in
``<run directory>.psimulate``, until the step runs again. A re-run starts the whole
simulation again; the step does not resume a partial run.

The step replaces its run directory on every successful run, so no other step may write
under it.

``options`` holds psimulate's options for ``run``, keyed by long option name with
``_`` for ``-``. ``project`` is required. ``backup_freq``, ``hardware``,
``max_attempts``, ``max_runtime``, ``max_workers``, ``peak_memory``, ``project``,
``queue``, ``slack_channel`` and ``slack_tag`` take a string or a number. ``no_slack``
takes ``True`` or ``False``. psimulate sends a Slack message when the run ends unless
you pass ``no_slack=True``. ``no_slack=True`` cannot be combined with ``slack_channel``
or ``slack_tag``, and ``slack_tag`` needs ``slack_channel``. The step sets the model
specification, the branches file, the artifact and the result directory itself, from
``spec``, ``branches``, ``artifact`` and ``produces``. Any other key is refused when the
step is defined.

This example runs a toy model with two draws and two seeds, so four simulations, and
sums the deaths by draw:

.. literalinclude:: ../../tests/frontend/simulation_example/pipeline.py
   :language: python
   :caption: pipeline.py

In a model's pipeline, ``env`` names the model's environment. The example uses
``sys.prefix``, the environment that ``dagger`` runs in, so that it needs no other
environment; that environment must then have ``vivarium-psimulate`` with its
``cluster`` extra.

When a step fails
-----------------

pytask prints a failure report for each failed step. Its traceback starts in the
step's own code, and the report includes what the step printed. ``dagger`` then prints one
line per failed step, with its error's type and message, and lists the steps that did
not run because a step they depend on failed. The traceback is shown only once. Steps
that do not depend on a failed step still run.

For a notebook step whose notebook raised an error, the line names the notebook, the
error, the cell it was raised in, numbered as papermill and Jupyter number it, and the
path of the executed notebook, which keeps the outputs of every cell that ran and the
error's traceback. For example:

.. code-block:: text

   Step 'summarize' failed: notebook summarize.ipynb raised KeyError: 'no_such_column' in cell In [3]. The executed notebook, with the error, is at /home/user/model/output/run1/executed/summarize.ipynb.

When the error's message has more than one line, the line gives only the first one that
is not blank. The executed notebook has the whole message.

If the notebook stopped before it recorded an error, for example because its environment
does not have papermill, the line says so and gives the exit status of the step's
command, or the signal that stopped it (for example SIGKILL). For any other step that
runs a shell command, the line gives the command's exit status, or the signal that
stopped it. In both cases the command itself and its output are in pytask's report for
the step, not in ``dagger``'s line.

For a simulation step in which some simulations did not finish, the line gives how many
finished, where the partial run is, and whether the previous complete run was kept. For
example:

.. code-block:: text

   Step 'sims/toy' failed: psimulate finished 3 of 4 simulations; the partial run is at /home/user/model/output/example/sims/toy.psimulate/toy_model/2026_11_10_09_30_00. The previous complete run at /home/user/model/output/example/sims/toy was kept.

If the step's command failed before the check wrote a message, for example because
psimulate itself failed, the line says only that the command failed, with its exit
status, where psimulate's logs are, and whether the previous complete run was kept. If
the check itself failed with an unexpected error, the line names the check and the
error.

If pytask stops the build for another reason, for example because its configuration
file or lockfile under the root cannot be read or a pytask plugin failed, pytask's
output shows the cause and ``dagger`` names pytask's exit code. (``dagger force`` reads the
lockfile itself before pytask starts, so there a lockfile that pytask cannot parse exits
with status 2.) If reading or writing pytask's state under the root fails, for example
because the disk is full or ``dagger force`` cannot open the lockfile, ``dagger`` names the
root and the error. Each of these exits with status 1, even when it happens before any
step has run. If a step ends the Python
process, for example by calling ``sys.exit``, the build stops there and ``dagger`` names
the step that exited and its exit code.

If you interrupt a run (Ctrl-C), ``dagger`` says that it was interrupted. The steps that
finished before the interrupt are recorded, so running the same command again
continues the run.

Exit status
-----------

``dagger`` exits with one of these codes, so a script can chain it, for example
``dagger run -c pipeline.py --local && next-command``:

====  ===================================================================================
Code  Meaning
====  ===================================================================================
0     The command succeeded.
1     A step failed or exited, or the build stopped for another reason, possibly
      before any step ran.
2     The definition or the command is wrong, and nothing ran.
130   The command was interrupted.
====  ===================================================================================

Errors in a definition or a command
-----------------------------------

Each of these exits with status 2 before any step runs:

- The definition cannot be found, has no function of the given name, or the function
  does not return a ``Pipeline``. ``dagger`` prints a one-line message.
- The definition's code raises an error, or calls ``sys.exit``, while it is imported
  or while its function runs. ``dagger`` prints the traceback as described below. For a
  step that reads an artifact that no step produces and that is not a root input, the
  error names the step and the artifact key, with the closest key that does exist. If
  no key is close, it lists the keys registered so far instead, which are the root
  inputs and the products of the steps defined before the one that reads the key.
  A notebook step that passes a name its notebook's ``parameters`` cell does not
  assign, or passes names to a notebook with no such cell, is rejected with an error
  that names the step, the notebook and each name.
- Two steps produce the same artifact. ``dagger`` names the artifact key and the steps.
- A step name given to ``--until`` or ``dagger force`` is not a step of the pipeline.
  ``dagger`` lists the valid step names.
- A location's primary data fails the handoff check (see :doc:`data_handoff`).
  ``dagger`` prints only the check's message, which lists each problem by location.
- Two steps have the same name. ``dagger`` names it and says to give each step a
  different ``name=`` to tell the steps apart. This is reported first,
  even when ``--until`` or ``dagger force`` also names a step that does not exist.
- The pipeline cannot be built as defined, for example because a step declares an
  environment that does not exist or pytask cannot order the steps. ``dagger`` prints
  the reason.
- The pipeline's root cannot be created or written, for example because it is an
  existing file or a directory above it is not writable. ``dagger`` checks this before
  pytask starts, without creating anything, and names the root and the reason.

For an error raised by the definition's own code, ``dagger`` prints the traceback with
the frames of ``vivarium.dagger``, pytask, pluggy, click, and Python's import
machinery removed, followed by a line such as ``[3 framework frames hidden]``. The
traceback then starts in the definition file. If no frame would be left, ``dagger`` prints
the whole traceback, except for a syntax error in the definition file itself: then it
prints only the file, the line number, the line with a caret under the error, and the
``SyntaxError``, as Python does.

The full reference for the command and its options is in :mod:`vivarium.dagger.cli`.

Steps that run a command
------------------------

:func:`~vivarium.dagger.frontend.command_step.command_step` makes a step that runs a
shell command. Use it when the step needs another environment than the pipeline's, or
when the work is already a command-line tool or a script. A ``python_step`` always runs
in the pipeline's own process, so it cannot use another environment. For any other step,
use ``python_step``.

This example puts a command step between two Python steps. The ``population`` step
writes a table, ``adults`` runs a script on it, and ``summary`` reads the script's
output:

.. literalinclude:: ../../tests/frontend/command_example/pipeline.py
   :language: python
   :caption: pipeline.py

The script, ``select_adults.py``, is in the same directory:

.. literalinclude:: ../../tests/frontend/command_example/select_adults.py
   :language: python
   :caption: select_adults.py

It runs like any other definition:

.. code-block:: text

   dagger preview -c pipeline.py
   dagger run -c pipeline.py --local

``inputs`` and ``produces`` map names to artifact keys. Each ``{name}`` in the command is
replaced by the absolute path of the input or product of that name, quoted for the
shell, so do not put quotes around a placeholder. The placeholders follow Python's
:meth:`str.format`, so a literal brace in the command must be doubled: write the shell
variable ``${VAR}`` as ``${{VAR}}``, and the ``awk`` program ``'{print $1}'`` as
``'{{print $1}}'``. A placeholder that is not an input or product name is refused when
the definition is loaded, with the valid names and, when one is close, the closest one.
The command does not have to use every name. An input that it does not name still runs
the step after the input's producer and makes the step stale when the input changes.

The command runs under ``/bin/sh``, not bash, both with ``--local`` and on a compute
node. On Ubuntu that is dash. Use POSIX sh syntax only: bash forms such as ``[[ ]]``,
``source``, ``set -o pipefail`` and ``{a,b}`` fail. In a pipe ``a | b``, the step's
status is the status of ``b`` alone, so a failure of ``a`` is lost. When you need bash,
write ``bash -c '...'`` or put the work in a script.

``env`` names the environment whose ``bin`` directories go first on ``PATH`` while the
command runs, for every part of a command such as ``a && b``. It is a conda env or venv
name, or the path to its prefix, and it is checked when the pipeline is built or
previewed. In a model, ``env`` names the other environment the step needs. The example
uses ``sys.prefix``, the pipeline's own environment, so that it runs anywhere. Without
``env``, the command runs with the ``PATH`` of the process that runs it: the ``dagger``
process for a local run, and the compute node's job for a run through Jobmon, which is
not the pipeline's environment. Name the environment whenever the command needs a tool
from it.

The command, with its paths, is part of the step's state, so a changed command makes
the step stale. A change to a script that the command runs does not, unless the script
is an input. Declare the script as a root input and pass it through a placeholder, as
the example does with ``{script}``. Like a ``python_step``, a command step re-runs on
every build for now, because a step cannot yet declare a fingerprint of its code; these
rules apply once it can.

Before the command runs, ``dagger`` creates the directory of each product. Products from
an earlier run are not removed first, so the command must write every product on every
run; a product left by an earlier run counts as written.

When the command exits with a status other than 0, the step fails, and ``dagger``'s
line for it gives the status, or the signal that stopped the command:

.. code-block:: text

   Step 'adults' failed: the command exited with status 1; its output is in pytask's report for the step.

The command and its output are in pytask's report for the step.
