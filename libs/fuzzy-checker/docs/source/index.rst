======================
Vivarium Fuzzy Checker
======================

This package provides the ``FuzzyChecker``, a tool for statistical "fuzzy"
checks of values that are subject to stochastic variation. It uses statistical
hypothesis testing to determine whether an observed value in a
`Vivarium <https://vivarium-engine.readthedocs.io/en/latest/>`_ simulation is
extreme enough to reject the null hypothesis that the simulation is behaving
correctly against a supplied verification or validation target.

It is one of the libraries in the
`vivarium-suite <https://github.com/ihmeuw/vivarium-suite>`_ monorepo, maintained
by the Institute for Health Metrics and Evaluation's Simulation Science team.

See :doc:`fuzzy_checking` for the statistical methodology. Broader background on
the team's verification and validation process lives in the
`Vivarium Research documentation
<https://vivarium-research.readthedocs.io/en/latest/model_design/designing_vivarium_model/results_processing_steps/index.html>`_.

.. toctree::
   :hidden:
   :maxdepth: 2

   self
   fuzzy_checking
