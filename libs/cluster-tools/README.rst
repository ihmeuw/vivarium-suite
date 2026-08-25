Vivarium Cluster Tools
=======================

.. image:: https://badge.fury.io/py/vivarium-cluster-tools.svg
    :target: https://badge.fury.io/py/vivarium-cluster-tools

.. image:: https://readthedocs.org/projects/vivarium-cluster-tools/badge/?version=latest
    :target: https://vivarium-cluster-tools.readthedocs.io/en/latest/?badge=latest
    :alt: Documentation Status

Vivarium cluster tools is a python package that makes running ``vivarium``
simulations at scale on a Slurm cluster easy.

**Supported Python versions: 3.11, 3.12, 3.13**

You can install ``vivarium-cluster-tools`` from PyPI with pip:

.. code-block:: bash

   pip install vivarium-cluster-tools

or build it from source by cloning the monorepo and installing this package:

.. code-block:: bash

   git clone https://github.com/ihmeuw/vivarium-suite.git
   cd vivarium-suite
   pip install libs/cluster-tools

.. note::

    The ``[cluster]`` extra (i.e. `pip install libs/cluster-tools[cluster]`) pulls
    in ``jobmon_installer_ihme`` (hosted on IHME's internal PyPI mirror), which
    is required to actually submit jobs on the IHME cluster. The base install
    (``pip install vivarium-cluster-tools``) resolves on public PyPI and is sufficient
    for read-only inspection of results / docs generation, but ``psimulate run``
    etc. need the cluster extra.

A simple example
----------------

If you have a ``vivarium`` model specification file defining a particular model,
you can use that along side a **branches file** to launch a run of many
simulations at once with variations in the input data, random seed, or with
different parameter settings.

.. code-block:: console

    psimulate run /path/to/model_specification.yaml /path/to/branches_file.yaml

The simplest branches file defines a count of input data draws and random seeds
to launch.

.. code-block:: yaml

    input_draw_count: 25
    random_seed_count: 10


This branches file defines a set of simulations for all combinations of 25
input draws and 10 random seeds and so would run, in total, 250 simulations.

You can also define a set of parameter variations to run your model over. Say
your original model specification looked something like

.. code-block:: yaml

    plugins:
      optional: ...

    components:
      vivarium.public_health:
        population:
          - BasePopulation()
        disease.models:
          - SIS('lower_respiratory_infections')
      my_lri_intervention:
        components:
          - GiveKidsVaccines()

    configuration:
      population:
        population_size: 1000
        age_start: 0
        age_end: 5
      lri_vaccine:
        coverage: 0.2
        efficacy: 0.8

Defining a simple model of lower respiratory infections and a vaccine
intervention. You could then write a branches file that varied over both
input data draws and random seeds, but also over different levels of coverage
and efficacy for the vaccine.  That file would look like

.. code-block:: yaml

    input_draw_count: 25
    random_seed_count: 10

    branches:
      - lri_vaccine:
          coverage: [0.0, 0.2, 0.4, 0.8, 1.0]
          efficacy: [0.4, 0.6, 0.8]

The branches file would overwrite your original ``lri_vaccine`` configuration
with each combination of coverage and efficacy in the branches file and launch
a simulation. More, it would run each coverage-efficacy pair in the branches
for each combination of input draw and random seed to produce 25 * 10 * 5 * 3 =
3750 unique simulations.

Multi-step workflows with ``dagger``
------------------------------------

For pipelines that chain several steps together, ``dagger`` runs a multi-step
Jobmon workflow defined in a YAML configuration file. Each step lists its
command and compute resources:

.. code-block:: yaml

    workflow:
      name: my_pipeline
      project: proj_simscience
      queue: all.q
      output_directory: /path/to/output
      steps:
        - name: launch_sims
          command: psimulate run /path/to/model_specification.yaml /path/to/branches_file.yaml
          resources:
            memory_gb: 4
            runtime: "01:00:00"
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

To read about more of the available features and get a better understanding
of how to correctly write your own branches files,


`Check out the docs! <https://vivarium-cluster-tools.readthedocs.io/en/latest/>`_
---------------------------------------------------------------------------------
