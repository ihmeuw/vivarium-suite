.. _dagger:

============================
Dagger: Multi-Step Workflows
============================

``dagger`` runs **multi-step Jobmon workflows** on the cluster. A workflow is
described by a single YAML file that lists an ordered series of *steps* -- each
step is a bash command, a parallel ``psimulate`` simulation, a pytest run, a
Python script, or a parameterized notebook. ``dagger`` builds one Jobmon
workflow from the file, runs the steps **in order**, and can resume a stopped
workflow from where it left off.

Use ``dagger`` when you have a pipeline (for example: run simulations, then
post-process the results, then render a report) that you want to launch and
resume as a single unit. For a single parallel simulation with no surrounding
steps, use ``psimulate`` directly (see :doc:`distributed_runner`).

.. contents::
   :local:
   :depth: 2


Quickstart
==========

Write a workflow file, ``workflow.yaml``:

.. code-block:: yaml

   workflow:
     name: example_workflow
     project: proj_simscience
     queue: all.q
     output_directory: /mnt/team/simulation_science/example_output
     default_environment: my_conda_env
     steps:
       - name: run_sims
         type: simulation
         resources:
           memory_gb: 3
           runtime: "24:00:00"
         args:
           model_specification: /path/to/model_specification.yaml
           branch_configuration: /path/to/branches.yaml
           artifact_path: /path/to/artifact.hdf
       - name: postprocess
         type: python
         resources:
           memory_gb: 10
           runtime: "01:00:00"
         args:
           path: /path/to/scripts/postprocess.py
           keyword_args:
             results_dir: /mnt/team/simulation_science/example_output

Launch it:

.. code-block:: bash

   dagger run --config workflow.yaml

``dagger`` runs ``run_sims`` to completion, then runs ``postprocess``. If the
workflow stops partway through (failure, timeout, manual cancel), resume it:

.. code-block:: bash

   dagger restart /mnt/team/simulation_science/example_output

See :ref:`dagger-semantics` for what ``restart`` does and how outputs are laid
out on disk.


.. _dagger-yaml-schema:

Workflow YAML schema
====================

Every workflow file has a single top-level ``workflow:`` key whose value is a
mapping of workflow-level fields plus a ``steps`` list.

.. code-block:: yaml

   workflow:
     name: ...
     project: ...
     queue: ...
     output_directory: ...
     default_environment: ...   # optional
     max_attempts: ...          # optional
     steps:
       - ...                    # one or more steps


Workflow-level fields
---------------------

``name``, ``project``, ``queue``, and ``output_directory`` are each required
*overall*, but may be supplied either in the YAML file **or** via a CLI
override (the CLI value wins). ``steps`` must be provided in the file and must
not be empty.

.. list-table::
   :header-rows: 1
   :widths: 22 12 66

   * - Field
     - Required
     - Description
   * - ``name``
     - yes
     - Workflow name shown in Jobmon. CLI override: ``--name/-n``.
   * - ``project``
     - yes
     - Cluster project to charge, e.g. ``proj_simscience``. CLI override:
       ``--project/-P``.
   * - ``queue``
     - yes
     - Cluster queue to submit to, e.g. ``all.q``. CLI override:
       ``--queue/-q``.
   * - ``output_directory``
     - yes
     - Top-level directory for all workflow outputs (relative or absolute).
       CLI override: ``--output-directory/-o``. See :ref:`dagger-output-layout`.
   * - ``default_environment``
     - no
     - Conda environment used for any step that does not set its own
       ``environment``. CLI override: ``--default-environment/-e``.
   * - ``max_attempts``
     - no
     - Maximum Jobmon attempts per task before it is marked failed. Defaults to
       ``2``. CLI override: ``--max-attempts/-m``.
   * - ``steps``
     - yes
     - Ordered list of steps. Step ``name`` values must be unique.


Step common fields
------------------

Every step, regardless of type, accepts:

.. list-table::
   :header-rows: 1
   :widths: 22 12 66

   * - Field
     - Required
     - Description
   * - ``name``
     - yes
     - Unique name for the step within the workflow.
   * - ``resources``
     - yes
     - Compute resources for the step's tasks (see below).
   * - ``environment``
     - no
     - Conda environment for this step. Overrides ``default_environment``.

The ``resources`` block:

.. list-table::
   :header-rows: 1
   :widths: 24 12 64

   * - Key
     - Required
     - Description
   * - ``memory_gb``
     - yes
     - Memory request in GB.
   * - ``runtime``
     - no
     - Maximum runtime as ``hh:mm:ss``. Default ``"01:00:00"``. Quote it so
       YAML does not parse it as a sexagesimal number.
   * - ``cores``
     - no
     - CPU cores to request. Default ``1``.
   * - ``project``
     - no
     - Per-step project override. Falls back to the workflow ``project``.
   * - ``queue``
     - no
     - Per-step queue override. Falls back to the workflow ``queue``.
   * - ``hardware``
     - no
     - List of hardware types to target, e.g. ``["r650"]``.
   * - ``requires_archive_node``
     - no
     - Whether to require an archive node. Default ``false``.


Step types
----------

A step's type is determined one of two ways:

* a top-level ``command:`` field makes it a **bash** step; or
* an explicit ``type:`` field selects one of ``simulation``, ``pytest``,
  ``python``, or ``notebook``.

All non-bash types take their type-specific options under an ``args:`` block.

bash
~~~~

Runs a shell command. Provide ``command:`` at the top level of the step (no
``args`` block). ``type: bash`` is implied and may be omitted.

.. code-block:: yaml

   - name: post_analysis
     command: python scripts/analyze.py --input /results
     environment: analysis_env
     resources:
       memory_gb: 20
       runtime: "02:00:00"
       cores: 2

simulation
~~~~~~~~~~

Runs a parallel ``psimulate`` simulation as a workflow step.

.. list-table::
   :header-rows: 1
   :widths: 28 12 60

   * - ``args`` key
     - Required
     - Description
   * - ``model_specification``
     - yes
     - Path to the model specification YAML.
   * - ``branch_configuration``
     - yes
     - Path to the branch configuration YAML.
   * - ``artifact_path``
     - no
     - Path to the data artifact. Overrides any artifact path in the model
       specification or branch configuration.
   * - ``backup_freq``
     - no
     - Backup frequency in seconds. Defaults to ``1800`` (30 minutes).
   * - ``sim_verbosity``
     - no
     - Per-simulation logging verbosity (``0``, ``1``, or ``2``).

.. code-block:: yaml

   - name: model_sims
     type: simulation
     resources:
       memory_gb: 3
       runtime: "24:00:00"
     args:
       model_specification: /path/to/model.yaml
       branch_configuration: /path/to/branches.yaml
       artifact_path: /path/to/artifact.hdf
       backup_freq: 1800
       sim_verbosity: 1

pytest
~~~~~~

Runs a pytest suite. Provide at least one of ``path`` or ``k``. ``path`` may be
a single string or a list of strings.

.. list-table::
   :header-rows: 1
   :widths: 24 12 64

   * - ``args`` key
     - Required
     - Description
   * - ``path``
     - one of path/k
     - Test path(s) to run. A string or a list of strings.
   * - ``k``
     - one of path/k
     - ``-k`` expression selecting tests by name.
   * - ``runslow``
     - no
     - Pass ``--runslow``. Default ``false``.

.. code-block:: yaml

   - name: pre_tests
     type: pytest
     resources:
       memory_gb: 8
       runtime: "01:00:00"
       cores: 4
     args:
       path:
         - tests/unit
         - tests/integration
       runslow: true

python
~~~~~~

Runs a Python script.

.. list-table::
   :header-rows: 1
   :widths: 26 12 62

   * - ``args`` key
     - Required
     - Description
   * - ``path``
     - yes
     - Path to the ``.py`` script to run.
   * - ``positional_args``
     - no
     - List of positional arguments passed to the script.
   * - ``keyword_args``
     - no
     - Mapping of ``--key value`` arguments passed to the script.

.. code-block:: yaml

   - name: postprocess
     type: python
     resources:
       memory_gb: 8
       runtime: "00:30:00"
     args:
       path: scripts/postprocess.py
       positional_args:
         - foo
         - bar
       keyword_args:
         input_dir: /mnt/results/model_29
         verbose: true
         num_workers: 4

notebook
~~~~~~~~

Executes a parameterized Jupyter notebook.

.. list-table::
   :header-rows: 1
   :widths: 24 12 64

   * - ``args`` key
     - Required
     - Description
   * - ``path``
     - yes
     - Path to the input ``.ipynb``.
   * - ``output_path``
     - yes
     - Path to write the executed ``.ipynb``.
   * - ``parameters``
     - no
     - Mapping of parameters injected into the notebook.
   * - ``cwd``
     - no
     - Working directory for execution. Defaults to the parent of ``path``.

.. code-block:: yaml

   - name: post_notebook
     type: notebook
     resources:
       memory_gb: 20
       runtime: "02:00:00"
     args:
       path: notebooks/results.ipynb
       output_path: /mnt/results/run_29/executed/results.ipynb
       parameters:
         model_dir: /mnt/results/run_29
         year: 2020


Running and restarting
======================

.. code-block:: bash

   # Launch a fresh workflow from a config file.
   dagger run --config workflow.yaml

   # Resume a stopped workflow from its output directory.
   dagger restart /path/to/output_directory

``dagger run`` accepts overrides for any workflow-level field
(``--name/-n``, ``--project/-P``, ``--queue/-q``, ``--output-directory/-o``,
``--default-environment/-e``, ``--max-attempts/-m``). ``dagger restart`` takes
the output directory as a positional argument and accepts ``--project/-P``,
``--queue/-q``, and ``--max-attempts/-m`` overrides.

Run ``dagger run --help`` or ``dagger restart --help`` for the full option list
(including the Slack notification flags ``--slack-channel`` / ``--slack-tag`` /
``--no-slack``).


.. _dagger-semantics:

How dagger behaves
==================

Steps run sequentially
----------------------

Steps execute **in the order listed**, with a full barrier between them: *every*
task in step *N* must finish before *any* task in step *N+1* starts. Parallelism
happens *within* a step -- for example, a ``simulation`` step fans out into many
parallel draw/seed tasks -- but sibling steps never overlap.

.. note::

   This is the key difference from ``psimulate``, which fans a single
   simulation out across the cluster. If you list two ``simulation`` steps,
   they run one after the other, not concurrently.


.. _dagger-output-layout:

Output directory layout
------------------------

There is a single workflow-level ``output_directory``. Every run writes
``configuration.yaml`` and ``.workflow_args`` there (used by ``dagger
restart``). ``simulation`` steps additionally create a
``<model_name>/<timestamp>/`` subdirectory beneath it, where ``model_name`` is
derived from the artifact (or model specification) -- for per-location
artifacts this is effectively the location name. The ``timestamp`` is recorded
in a ``.build_timestamp`` marker so that all simulation steps in the run share
it; that marker is **only written when the workflow contains a simulation
step**. Other step types write their worker logs and outputs directly under
``output_directory``.

.. code-block:: text

   output_directory/
   ├── configuration.yaml          # the resolved workflow config (for restart)
   ├── .workflow_args              # persisted Jobmon workflow id (for restart)
   ├── .build_timestamp            # shared run timestamp (simulation steps only)
   └── <model_name>/               # simulation steps only
       └── <timestamp>/            # one simulation step's results
           ├── results/
           ├── sim_backups/
           ├── metadata/
           └── logs/


Restart resumes the whole workflow
-----------------------------------

``dagger restart <output_directory>`` reloads ``configuration.yaml`` and the
persisted ``.workflow_args`` written by the original run, then resumes the
**entire** Jobmon workflow, skipping tasks that already completed. Tasks that
failed or never ran are retried.

There is no per-step or per-task restart: restart always operates on the whole
workflow. A task that already succeeded is *skipped*, so ``restart`` cannot be
used to deliberately re-run a step that finished successfully.


.. warning::

   **Running a second** ``dagger run`` **into a populated directory overwrites
   it.** ``configuration.yaml`` and ``.workflow_args`` are rewritten on every
   run, so a second run leaves the first one no longer restartable. And if the
   workflow contains a ``simulation`` step, that second run reuses the persisted
   ``.build_timestamp`` rather than creating a new timestamped run -- so it
   writes into the first run's ``<model_name>/<timestamp>/`` directory and
   **overwrites those results in place**.

   To guard against this, ``dagger run`` detects a previous run in the target
   ``output_directory`` (via the persisted ``.workflow_args``) and **prompts for
   confirmation before continuing**; answering ``n`` aborts without changing
   anything. Still, prefer a **fresh** ``output_directory`` for each new
   workflow, and use ``dagger restart`` (not a second ``dagger run``) to resume
   an interrupted one.
