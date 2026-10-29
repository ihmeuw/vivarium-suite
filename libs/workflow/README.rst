=================
Vivarium Workflow
=================

.. image:: https://badge.fury.io/py/vivarium-workflow.svg
    :target: https://badge.fury.io/py/vivarium-workflow

.. image:: https://readthedocs.org/projects/vivarium-workflow/badge/?version=latest
    :target: https://vivarium-workflow.readthedocs.io/en/latest/?badge=latest
    :alt: Documentation Status

This library builds a data pipeline from tasks that each declare their inputs and
outputs. It wires producers to consumers by artifact key and uses ``pytask`` to
decide which tasks are stale and to run them. It has four parts: the backend
(the task model, the build, and the run preview), the ``python_step`` frontend for
writing a step as a plain function, a bridge that runs a build's stale steps on compute
nodes as one Jobmon workflow, and a test harness for staleness scenarios. The bridge needs
the ``cluster`` extra: ``pip install "vivarium-workflow[cluster]"``.

**Supported Python versions: 3.11, 3.12, 3.13**

You can install ``vivarium-workflow`` from PyPI with pip:

.. code-block:: bash

   pip install vivarium-workflow

or build it from source by cloning the monorepo and installing this package:

.. code-block:: bash

   git clone https://github.com/ihmeuw/vivarium-suite.git
   cd vivarium-suite
   pip install libs/workflow

For broader monorepo development setup, see the monorepo README at
https://github.com/ihmeuw/vivarium-suite.

`Check out the docs! <https://vivarium-workflow.readthedocs.io/en/latest/>`_
----------------------------------------------------------------------------
