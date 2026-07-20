============================
Vivarium Auto Validation
============================

.. image:: https://badge.fury.io/py/vivarium-auto-validation.svg
    :target: https://badge.fury.io/py/vivarium-auto-validation

.. image:: https://readthedocs.org/projects/vivarium-auto-validation/badge/?version=latest
    :target: https://vivarium-auto-validation.readthedocs.io/en/latest/?badge=latest
    :alt: Documentation Status

This library provides tooling for automated verification and validation (V&V) of
Vivarium simulations, including data loading, measure comparison, and reporting.

**Supported Python versions: 3.10, 3.11**

You can install ``vivarium-auto-validation`` from PyPI with pip:

.. code-block:: bash

   pip install vivarium-auto-validation

or build it from source by cloning the monorepo and installing this package:

.. code-block:: bash

   git clone https://github.com/ihmeuw/vivarium-suite.git
   cd vivarium-suite
   pip install libs/auto-validation

Note that the validation feature depends on ``vivarium-inputs``, which is only
available from the IHME artifactory.

For broader monorepo development setup, see the monorepo README at
https://github.com/ihmeuw/vivarium-suite.

`Check out the docs! <https://vivarium-auto-validation.readthedocs.io/en/latest/>`_
-----------------------------------------------------------------------------------
