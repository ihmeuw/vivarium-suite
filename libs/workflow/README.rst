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
decide which tasks are stale and to run them. It has three parts: the backend
(the task model, the build, and the run preview), the frontend (``python_step`` for
writing a step as a plain function, ``notebook_step`` for running a notebook as a step,
a check of each location's delivered primary data, and verbs that run or preview a whole
pipeline by step name), and a test harness for staleness scenarios.
The ``vwf`` command runs, previews, or forces the steps of a pipeline definition.

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
