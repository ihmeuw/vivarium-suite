====================
Vivarium Build Utils
====================

Vivarium Build Utils contains shared build utilities for Simulation Science projects.

You can install ``vivarium_build_utils`` from PyPI with pip::

  $ pip install vivarium_build_utils

or build it from source with::

  $ git clone https://github.com/ihmeuw/vivarium_build_utils.git
  $ cd vivarium_build_utils
  $ conda create -n ENVIRONMENT_NAME
  $ pip install -e .

Overview
========

This repository provides:

- **`vars/`**: Jenkins shared library functions for continuous integration pipelines
- **`resources/`**: Shared Makefiles and build scripts for consistent build processes

Note: for help with the Make targets available to any environment with this repository
installed, run `make help` in the terminal.
