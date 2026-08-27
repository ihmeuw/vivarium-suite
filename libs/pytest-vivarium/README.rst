===============
pytest-vivarium
===============

A `pytest <https://docs.pytest.org/>`_ plugin providing the shared test
configuration used across the Institute for Health Metrics and Evaluation's
Simulation Science team's `Vivarium <https://vivarium-engine.readthedocs.io/en/latest/>`_
projects: the ``slow``/``weekly``/``cluster`` markers and their ``--runslow`` /
``--runweekly`` / ``--slurm-project`` options, a memory- and CPU-aware ceiling on
``pytest -n auto`` xdist workers, and the ``no_gbd_cache`` fixture.

It is one of the libraries in the
`vivarium-suite <https://github.com/ihmeuw/vivarium-suite>`_ monorepo.

**Supported Python versions: 3.11, 3.12, 3.13**

Installation
------------

.. code-block:: console

   $ pip install pytest-vivarium

pytest discovers the plugin automatically via its ``pytest11`` entry point; there
is nothing to import or enable.


`Check out the docs! <https://pytest-vivarium.readthedocs.io/en/latest/>`_
--------------------------------------------------------------------------
