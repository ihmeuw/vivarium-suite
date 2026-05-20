================
vivarium.artifact
================

Data artifact storage and access.

.. contents::
   :depth: 1

Overview
--------

A data artifact is an archive on disk that packages data. This package provides:

- ``Artifact``: the in-memory model wrapping the on-disk archive
- ``ArtifactException``: raised on inconsistent artifact use
- ``EntityKey``: the structured key type used to address artifact contents

The above names are re-exported at the package root (``from vivarium.artifact
import Artifact``).

.. note::
    
    The ``Manager`` and ``Interface`` classes that integrate artifacts into a
    vivarium simulation lifecycle live in the ``vivarium`` package at
    ``vivarium.framework.artifact``. Those classes import from this package.

Installation
------------

**Supported Python versions: 3.10, 3.11, 3.12, 3.13**

``vivarium-artifact`` is published on PyPI as part of the vivarium-suite monorepo:

.. code-block:: bash

   pip install vivarium-artifact

To build it from source, clone the monorepo and install from this package directory:

.. code-block:: bash

   git clone https://github.com/ihmeuw/vivarium-suite.git
   cd vivarium-suite
   pip install libs/artifact

For broader monorepo development setup, see the monorepo README at
https://github.com/ihmeuw/vivarium-suite.
