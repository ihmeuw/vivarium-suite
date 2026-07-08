===============
vivarium.engine
===============

.. image:: https://readthedocs.org/projects/vivarium-engine/badge/?version=latest
    :target: https://vivarium-engine.readthedocs.io/en/latest/?badge=latest
    :alt: Latest Docs

.. image:: https://zenodo.org/badge/96817805.svg
   :target: https://zenodo.org/badge/latestdoi/96817805

``vivarium-engine`` is a simulation framework written using standard scientific
Python tools.

Installation
------------

**Supported Python versions: 3.10, 3.11, 3.12**

.. note::

    If you have an older version of ``vivarium`` installed, you should uninstall
    it before installing ``vivarium-engine``. If you have both packages installed,
    you may see deprecation warnings when importing from ``vivarium`` and it's possible
    that some imports will break if they hit the old package's on-disk location
    instead of the new one.

You can install ``vivarium-engine`` from PyPI with pip:

.. code-block:: bash

   pip install vivarium-engine

or build it from source with

.. code-block:: bash

   git clone https://github.com/ihmeuw/vivarium-suite.git
   cd vivarium-suite
   pip install libs/engine


This will make the ``vivarium-engine`` library available to python and install a
command-line executable called ``simulate`` that you can use to verify your
installation with

.. code-block:: bash

   simulate test

For broader monorepo development setup, see the monorepo README at
https://github.com/ihmeuw/vivarium-suite.

`Check out the docs! <https://vivarium-engine.readthedocs.io/en/latest/>`_
--------------------------------------------------------------------------
