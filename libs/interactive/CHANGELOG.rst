**1.1.0b2 - 11/04/26**

- Require ``vivarium-dependencies>=2.0.0``, which raises the floors of this library's
  dependencies to ``numpy>=2.1.0``, ``pandas>=3.0.0``

**1.1.0b1 - 11/03/26**

- Add ``population_size``, ``seed``, ``draw``, ``artifact_path``, ``start_time``, and ``end_time`` keyword parameters to ``Simulation``

**1.0.1 - 10/28/26**

- Subclass engine's renamed ``BaseSimulation`` and require ``vivarium-engine`` 6

**1.0.0 - 10/28/26**

- Initial release: ``InteractiveContext`` split out of ``vivarium-engine`` into its own package and renamed to ``Simulation``
- ``log_progress``, ``run_from_ipython`` and ``ObservationError`` now live here alongside ``Simulation``
