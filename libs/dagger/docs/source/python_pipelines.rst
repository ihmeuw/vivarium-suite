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
- Two steps produce the same artifact. ``dagger`` names the artifact key and the steps.
- A step name given to ``--until`` or ``dagger force`` is not a step of the pipeline.
  ``dagger`` lists the valid step names.
- A location's primary data fails the handoff check (see :doc:`data_handoff`).
  ``dagger`` prints only the check's message, which lists each problem by location.
- Two steps have the same name. ``dagger`` names it and says to pass ``name=`` to
  ``python_step`` or ``notebook_step`` to tell the steps apart. This is reported first,
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
