===============
Vivarium Dagger
===============

This package builds and runs multi-step data pipelines. It provides
:ref:`dagger <dagger>`, which runs a pipeline that a YAML file describes as one
Jobmon workflow on the cluster.

It also builds a pipeline from tasks that each declare their inputs and outputs.
It wires producers to consumers by file key and hands the tasks to
`pytask <https://pytask-dev.readthedocs.io/en/stable/>`_, which decides which tasks
are stale and runs them in order. A preview reports which steps a build would run,
and why, without running them. ``dagger`` also runs, previews, or forces the steps of
a pipeline definition from the command line (see :doc:`python_pipelines`).

It is one of the libraries in the
`vivarium-suite <https://github.com/ihmeuw/vivarium-suite>`_ monorepo, maintained
by the Institute for Health Metrics and Evaluation's Simulation Science team.

.. toctree::
   :hidden:
   :maxdepth: 2

   self
   dagger
   root_inputs
   python_pipelines
   api_reference/index
