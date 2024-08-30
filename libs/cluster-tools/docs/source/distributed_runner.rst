.. toctree::
   :maxdepth: 2
   :caption: Contents:

Running simulations in parallel
===============================

Once you successfully create a simulation specification and branch file it is
time to use the distributed runner. Recall that we run a single simulation with
a model specification file in this way:

.. code-block:: console

    simulate run <PATH-TO-MODEL-SPECIFICATION-YAML>

Very similar to this single-simulation ``simulate`` command, ``vivarium-cluster-tools`` includes a 
``psimulate`` command for running multiple simulations in parallel:

.. code-block:: console

    psimulate run <PATH-TO-MODEL-SPECIFICATION-YAML>  <PATH-TO-BRANCH-SPECIFICATION-YAML>

In addition to providing the model specification and branches filepaths, you *must* provide an 
output directory with the ``-o`` flag and which project you'd like to run on with the ``-P`` flag.

.. code-block:: console

    psimulate run <PATH-TO-MODEL-SPECIFICATION-YAML> <PATH-TO-BRANCH-SPECIFICATION-YAML> -o <PATH-TO-OUTPUT-DIRECTORY> -P <PROJECT>

``psimulate run`` also provides various *optional* flags which you can use to configure options for the run. These are:

.. list-table:: **Available** ``psimulate run`` **options**
    :header-rows: 1
    :widths: 30, 40

    *   - Option
        - Description
    *   - | **-\-artifact_path** or **-i**
        - | The path to a directory containing the artifact data file that the 
          | model requires. This is only required if the model specification
          | file does not contain the artifact path or you want to override it.
    *   - | **-\-pdb**
        - | If an error occurs, drop into the python debugger.
    *   - | **-\-verbose** or **-v**
        - | Report each time step as it occurs during the run.
    *   - | **-\-backup-freq**
        - | The frequency with which to save a backup of the simulation state to disk.
    *   - | **-\-no-batch**
        - | Do not write results in batches; write them as they come in.
    *   - | **-\-redis**
        - | Number of redis databases to use.
    *   - | **-\-max-workers** or **-w**
        - | The maximum number of workers to run concurrently.
    *   - | **-\-hardware** or **-h**
        - | A comma-separated list of the specific cluster hardware to run on.
          | Refer to the --help for currently-supported opions.
    *   - | **-\-peak-memory** or **-m**
        - | The maximum amount of memory to request per worker (in GB).
    *   - | **-\-max-runtime** or **-r**
        - | The maximum amount of time to request per worker (hh:mm:ss). Note that
          | the session you are launching the ``psimulate run`` from must also
          | be able to live at least as long as this value (and this does not account)
          | for the time jobs may spend in PENDING.
    *   - | **-\-queue** or **-q**
        - | The queue to submit jobs to.
    *   - | **-\-help**
        - | Print a help message and exit.

You can see a description of any of the available commands by using the **-\-help** flag, e.g. ``psimulate --help`` 
or ``psimulate run --help``.

Restarting a Simulation
-----------------------

If your ``psimulate run`` has jobs that failed to complete, you can restart them using ``psimulate restart``. 
You must specify the results directory that includes the partially completed jobs as well as the project 
you want to use for the restart.

.. code-block:: console

    psimulate restart <PATH-TO-PREVIOUS-RESULTS-DIRECTORY> -P <PROJECT>

Many of the same optional flags exist for ``psimulate restart`` as for ``psimulate run``. You can see a description of
these by using the ``psimulate restart --help``.

Expanding a Simulation
----------------------

If you wish to expand an existing simulation by running new simulations with additional input draws and/or random seeds, 
you can do so using ``psimulate expand``. Just like for ``psimulate restart``, you must specify the results directory 
that includes the results that you'd like to expand as well as a project. Further, you must specify the number of
additional draws and/or seeds you'd like to add to the simulation.

.. code-block:: console

    psimulate expand <PATH-TO-PREVIOUS-RESULTS-DIRECTORY> -P <PROJECT> --add-draws 10 --add-seeds 5

You can use one or both of ``--add-draws`` and ``--add-seeds`` to expand your simulation. Any previous results will not
be overwritten, but any additional simulations resulting from the new input draws and/or random seeds will be run.

As before, use ``psimulate expand --help`` to see a description of the available options.
