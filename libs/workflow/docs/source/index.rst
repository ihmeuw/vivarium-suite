=================
Vivarium Workflow
=================

This package builds a data pipeline from tasks that each declare their inputs and
outputs. It wires producers to consumers by artifact key and hands the tasks to
`pytask <https://pytask-dev.readthedocs.io/en/stable/>`_, which decides which tasks
are stale and runs them in order. A preview reports which steps a build would run,
and why, without running them. The :doc:`vwf command <vwf>` runs, previews, or forces
the steps of a pipeline definition from the command line.

It is one of the libraries in the
`vivarium-suite <https://github.com/ihmeuw/vivarium-suite>`_ monorepo, maintained
by the Institute for Health Metrics and Evaluation's Simulation Science team.

.. toctree::
   :hidden:
   :maxdepth: 2

   self
   data_handoff
   vwf
   api_reference/index
