===============
Vivarium Dagger
===============

.. image:: https://badge.fury.io/py/vivarium-dagger.svg
    :target: https://badge.fury.io/py/vivarium-dagger

.. image:: https://readthedocs.org/projects/vivarium-dagger/badge/?version=latest
    :target: https://vivarium-dagger.readthedocs.io/en/latest/?badge=latest
    :alt: Documentation Status

This library builds and runs multi-step data pipelines. It provides ``dagger``,
a command that runs a pipeline described in a YAML file as one Jobmon workflow
on a Slurm cluster.

It also builds a data pipeline from tasks that each declare their inputs and
outputs. It wires producers to consumers by artifact key and uses ``pytask`` to
decide which tasks are stale and to run them. This part has three pieces: the
backend (the task model, the build, and the run preview), the frontend
(``python_step`` for writing a step as a plain function, ``notebook_step`` for
running a notebook as a step, a check of each location's delivered primary data,
and verbs that run or preview a whole pipeline by step name), and a test harness
for staleness scenarios. ``dagger`` also runs, previews, or forces the steps of a
pipeline definition.

**Supported Python versions: 3.11, 3.12, 3.13**

You can install ``vivarium-dagger`` from PyPI with pip:

.. code-block:: bash

   pip install vivarium-dagger

or build it from source by cloning the monorepo and installing this package:

.. code-block:: bash

   git clone https://github.com/ihmeuw/vivarium-suite.git
   cd vivarium-suite
   pip install libs/dagger

.. note::

    The ``[cluster]`` extra (``pip install vivarium-dagger[cluster]``) pulls in
    ``jobmon_installer_ihme`` from IHME's internal PyPI mirror. ``dagger`` needs
    it to submit jobs. The ``[simulation]`` extra installs ``vivarium-psimulate``,
    which a workflow with a ``simulation`` step needs.

Multi-step workflows with ``dagger``
------------------------------------

A ``dagger`` workflow file lists steps that run in order. Each step gives its
command and its compute resources:

.. code-block:: yaml

    workflow:
      name: my_pipeline
      project: proj_simscience
      queue: all.q
      output_directory: /path/to/output
      steps:
        - name: run_sims
          type: simulation
          resources:
            memory_gb: 3
            runtime: "24:00:00"
          args:
            model_specification: /path/to/model_specification.yaml
            branch_configuration: /path/to/branches.yaml
        - name: post_process
          command: my_post_processing_script /path/to/output
          resources:
            memory_gb: 8
            runtime: "00:30:00"

Launch the workflow with

.. code-block:: console

    dagger run -c /path/to/workflow.yaml

If a run fails partway through, resume it from its output directory, skipping
steps that already completed, with

.. code-block:: console

    dagger restart /path/to/output

`Check out the docs! <https://vivarium-dagger.readthedocs.io/en/latest/>`_
--------------------------------------------------------------------------
