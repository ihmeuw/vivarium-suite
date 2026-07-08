==========================
Installing vivarium-engine
==========================

.. contents::
   :depth: 1
   :local:
   :backlinks: none

.. highlight:: console

Overview
--------

``vivarium-engine`` is written in `Python`__ and supports Python 3.10-3.12.

__ http://docs.python-guide.org/en/latest/

.. _install-pypi:

Installation from PyPI
----------------------

``vivarium-engine`` is published on the `Python Package Index
<https://pypi.org/project/vivarium-engine/>`_. The preferred tool for
installing packages from *PyPI* is :command:`pip`, which ships with all
modern versions of Python.

.. note::

   If you had the legacy ``vivarium`` package installed (pre-monorepo),
   uninstall it before installing ``vivarium-engine`` to avoid file
   conflicts on the shared ``vivarium`` namespace::

      $ pip uninstall vivarium

On Linux or MacOS, run::

   $ pip install -U vivarium-engine

On Windows, open *Command Prompt* and run the same command.

.. code-block:: doscon

   C:\> pip install -U vivarium-engine

After installation, run :command:`simulate test`. This will run a test
simulation packaged with the framework and validate that everything is
installed correctly.

Installation from source
------------------------

``vivarium-engine`` lives in the `vivarium-suite monorepo`__. Clone the
monorepo and install from this package directory::

    $ git clone https://github.com/ihmeuw/vivarium-suite.git
    $ cd vivarium-suite
    $ pip install libs/engine

For broader monorepo development setup, see the monorepo README.

__ https://github.com/ihmeuw/vivarium-suite

.. highlight:: default
