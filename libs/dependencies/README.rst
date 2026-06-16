=====================
Vivarium Dependencies
=====================

.. image:: https://badge.fury.io/py/vivarium-dependencies.svg
    :target: https://badge.fury.io/py/vivarium-dependencies

Vivarium Dependencies is a code-less convenience metapackage that defines
the dependency pins commonly shared across the Vivarium ecosystem.

Usage
=====

A downstream repository can pull in groups of pins by referencing one or
more extras of ``vivarium-dependencies`` in its pyproject.toml, e.g.:

.. code-block:: toml

   [project]
   dependencies = [
       "vivarium-dependencies[numpy,pandas,scipy]",
       ...
   ]

   [project.optional-dependencies]
   interactive = ["vivarium-dependencies[interactive]"]
   lint = ["vivarium-dependencies[lint]"]

The package itself ships no Python modules; ``pip install vivarium-dependencies``
is not useful on its own. The point is the extras.

Installation
============

You can install ``vivarium-dependencies`` from PyPI:

.. code-block:: bash

   pip install vivarium-dependencies

or build it from source by cloning the monorepo and installing this lib:

.. code-block:: bash

   git clone https://github.com/ihmeuw/vivarium-suite.git
   cd vivarium-suite
   pip install libs/dependencies

For broader monorepo development setup, see the monorepo README at
https://github.com/ihmeuw/vivarium-suite.
