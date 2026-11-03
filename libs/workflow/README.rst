=================
Vivarium Workflow
=================

.. image:: https://badge.fury.io/py/vivarium-workflow.svg
    :target: https://badge.fury.io/py/vivarium-workflow

.. image:: https://readthedocs.org/projects/vivarium-workflow/badge/?version=latest
    :target: https://vivarium-workflow.readthedocs.io/en/latest/?badge=latest
    :alt: Documentation Status

This library builds and runs multi-step data pipelines. It provides ``dagger``,
a command that runs a pipeline described in a YAML file as one Jobmon workflow
on a Slurm cluster.

**Supported Python versions: 3.11, 3.12, 3.13**

You can install ``vivarium-workflow`` from PyPI with pip:

.. code-block:: bash

   pip install vivarium-workflow

or build it from source by cloning the monorepo and installing this package:

.. code-block:: bash

   git clone https://github.com/ihmeuw/vivarium-suite.git
   cd vivarium-suite
   pip install libs/workflow

.. note::

    The ``[cluster]`` extra (``pip install vivarium-workflow[cluster]``) pulls in
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

`Check out the docs! <https://vivarium-workflow.readthedocs.io/en/latest/>`_
----------------------------------------------------------------------------
