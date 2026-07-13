GBD Mapping
===========

.. image:: https://badge.fury.io/py/vivarium-gbd-mapping.svg
    :target: https://badge.fury.io/py/vivarium-gbd-mapping

.. image:: https://readthedocs.org/projects/vivarium-gbd-mapping/badge/?version=latest
    :target: https://vivarium-gbd-mapping.readthedocs.io/en/latest/?badge=latest
    :alt: Latest Docs

Mapping of Global Burden of Disease (GBD) entities to their metadata.

There are two packages offered in this distribution.  The first, the ``gbd_mapping_generator``
is a set of scripts that define templates and data gathering code used to produce the second, the ``gbd_mapping``.
The ``gbd_mapping_generator`` package will not function without access to the IHME cluster and some of our
internally used data access libraries. Mapping updates are managed by an automated toolchain, so this shouldn't
be an issue.

The ``gbd_mapping`` is a programmatically accessible (and TAB-complete-able) set of mappings for GBD entities
including:

 - Causes
 - Risks
 - Covariates
 - Etiologies
 - Sequelae

**Supported Python versions: 3.10, 3.11**

You can install ``vivarium-gbd-mapping`` from PyPI with pip:

.. code-block:: bash

   pip install vivarium-gbd-mapping

or build it from source by cloning the monorepo and installing this package:

.. code-block:: bash

   git clone https://github.com/ihmeuw/vivarium-suite.git
   cd vivarium-suite
   pip install libs/gbd-mapping


Development and Mapping Generation
++++++++++++++++++++++++++++++++++

In order to generate or regenerate the mappings from data, you must have access to
the Institute for Health Metrics and Evaluation cluster and internal PyPI server.
Contact the vivarium developers at vivarium.dev@gmail.com or open an issue at
https://github.com/ihmeuw/vivarium-suite for further instructions.

Given proper permissions, you can set up this library in development mode with

.. code-block:: bash

   git clone https://github.com/ihmeuw/vivarium-suite.git
   cd vivarium-suite
   pip install -e 'libs/gbd-mapping[dev]'


`Check out the docs! <https://vivarium-gbd-mapping.readthedocs.io/en/latest/>`_
-------------------------------------------------------------------------------
