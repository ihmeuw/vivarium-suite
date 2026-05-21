================
vivarium.engine
================

.. image:: https://readthedocs.org/projects/vivarium/badge/?version=latest
    :target: https://vivarium.readthedocs.io/en/latest/?badge=latest
    :alt: Latest Docs

.. image:: https://zenodo.org/badge/96817805.svg
   :target: https://zenodo.org/badge/latestdoi/96817805

Vivarium is a simulation framework written using standard scientific Python
tools. ``vivarium-engine`` is the core simulation lifecycle, component model,
and runtime: it is what the rest of the ``vivarium-*`` ecosystem builds on.

Installation
------------

**Supported Python versions: 3.10, 3.11, 3.12, 3.13**

``vivarium-engine`` lives in the vivarium-suite monorepo. To build it from
source, clone the monorepo and install from this package directory:

.. code-block:: bash

   git clone https://github.com/ihmeuw/vivarium-suite.git
   cd vivarium-suite
   pip install libs/engine

This installs the ``vivarium.engine`` import package and a command-line
executable ``simulate`` that you can use to verify your installation:

.. code-block:: bash

   simulate test

For broader monorepo development setup, see the monorepo README at
https://github.com/ihmeuw/vivarium-suite.

`Check out the docs! <https://vivarium.readthedocs.io/en/latest/>`_
-------------------------------------------------------------------
