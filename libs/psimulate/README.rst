==================
Vivarium psimulate
==================

.. image:: https://badge.fury.io/py/vivarium-psimulate.svg
    :target: https://badge.fury.io/py/vivarium-psimulate

.. image:: https://readthedocs.org/projects/vivarium-psimulate/badge/?version=latest
    :target: https://vivarium-psimulate.readthedocs.io/en/latest/?badge=latest
    :alt: Documentation Status

This library provides ``psimulate``, a command that runs many ``vivarium``
simulations in parallel on a Slurm cluster, and ``vipin``, a command that
summarizes the performance logs of a ``psimulate`` run. Both used to be part of
``vivarium-cluster-tools``, which keeps the shared cluster, Jobmon, logging and
notification code that they use.

**Supported Python versions: 3.11, 3.12, 3.13**

You can install ``vivarium-psimulate`` from PyPI with pip:

.. code-block:: bash

   pip install vivarium-psimulate

or build it from source by cloning the monorepo and installing this package:

.. code-block:: bash

   git clone https://github.com/ihmeuw/vivarium-suite.git
   cd vivarium-suite
   pip install libs/psimulate

.. note::

    The ``[cluster]`` extra (``pip install vivarium-psimulate[cluster]``) pulls in
    ``jobmon_installer_ihme`` from IHME's internal PyPI mirror. ``psimulate run``
    and the other commands that submit jobs need it. The base install resolves on
    public PyPI and is enough to inspect results or build the docs.

A simple example
----------------

If you have a ``vivarium`` model specification file that defines a model, you
can use it with a **branches file** to launch many simulations at once, with
variations in the input data, the random seed or the parameter settings.

.. code-block:: console

    psimulate run -M /path/to/model_specification.yaml -B /path/to/branches_file.yaml \
        -o /path/to/results -P proj_simscience

The simplest branches file gives a count of input data draws and random seeds
to launch.

.. code-block:: yaml

    input_draw_count: 25
    random_seed_count: 10

This branches file defines one simulation for each combination of 25 input
draws and 10 random seeds, so it runs 250 simulations in total.

A branches file can also vary the model's configuration. The model
specification below defines a model of lower respiratory infections and a
vaccine intervention.

.. code-block:: yaml

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

This branches file varies the vaccine's coverage and efficacy as well as the
input draws and random seeds.

.. code-block:: yaml

    input_draw_count: 25
    random_seed_count: 10

    branches:
      - lri_vaccine:
          coverage: [0.0, 0.2, 0.4, 0.8, 1.0]
          efficacy: [0.4, 0.6, 0.8]

Each combination of coverage and efficacy replaces the ``lri_vaccine``
configuration in the model specification, and each runs for every combination
of input draw and random seed. That makes 25 * 10 * 5 * 3 = 3750 simulations.

`Check out the docs! <https://vivarium-psimulate.readthedocs.io/en/latest/>`_
-----------------------------------------------------------------------------
