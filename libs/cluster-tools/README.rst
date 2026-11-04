=======================
Vivarium Cluster Tools
=======================

.. image:: https://badge.fury.io/py/vivarium-cluster-tools.svg
    :target: https://badge.fury.io/py/vivarium-cluster-tools

.. image:: https://readthedocs.org/projects/vivarium-cluster-tools/badge/?version=latest
    :target: https://vivarium-cluster-tools.readthedocs.io/en/latest/?badge=latest
    :alt: Documentation Status

This library holds the shared code that Vivarium's cluster tools use:

- validation of Slurm projects, queues, runtimes and hardware, and the resource
  specification for a cluster job;
- a small facade over the Jobmon client, and the resolution of the conda env or
  venv that a Jobmon task runs in;
- ``build_artifacts_in_parallel``, which builds a model's data artifacts for many
  locations as one Jobmon workflow;
- Slack notifications when a workflow finishes;
- shared Click options and logging configuration.

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

    The ``[cluster]`` extra (``pip install vivarium-cluster-tools[cluster]``) pulls
    in ``jobmon_installer_ihme`` from IHME's internal PyPI mirror. Code that submits
    Jobmon workflows, such as ``build_artifacts_in_parallel``, needs it. The base
    install resolves on public PyPI.

`Check out the docs! <https://vivarium-cluster-tools.readthedocs.io/en/latest/>`_
---------------------------------------------------------------------------------
