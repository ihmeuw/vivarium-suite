===============
pytest-vivarium
===============

``pytest-vivarium`` is a `pytest <https://docs.pytest.org/>`_ plugin providing the
shared test configuration used across the
`vivarium-suite <https://github.com/ihmeuw/vivarium-suite>`_ monorepo and the
Institute for Health Metrics and Evaluation's Simulation Science team's model
repositories. Installing it as a test dependency auto-registers everything below
via pytest's ``pytest11`` entry point - there is nothing to import.

Markers and options
--------------------

``slow``
    Mark a test as slow. Skipped unless ``--runslow`` is passed.

``weekly``
    Mark a test as part of the weekly suite. Skipped unless ``--runweekly`` is
    passed or it is the designated slow-test day (Sunday).

``cluster``
    Mark a test as requiring a SLURM cluster. Skipped automatically when
    ``sbatch`` is not on the ``PATH``. ``--slurm-project`` sets the SLURM project
    (default ``proj_simscience``).

Bounded ``-n auto`` workers
---------------------------

The plugin implements ``pytest_xdist_auto_num_workers`` so that ``pytest -n auto``
does not oversubscribe large shared nodes. The worker count targets a small
ceiling and is clamped to the process's usable CPUs and to the memory available
to it - reading both node free memory and any cgroup (for example a SLURM
``--mem``) limit. The resolved plan is printed in the run header.

The ``no_gbd_cache`` fixture
----------------------------

``no_gbd_cache`` disables ``vivarium_gbd_access`` caching for test isolation. It
is not ``autouse``; wrap it in an ``autouse`` fixture in your own ``conftest.py``
to apply it broadly.

.. toctree::
   :hidden:
   :maxdepth: 2

   self
