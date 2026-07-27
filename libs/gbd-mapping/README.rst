GBD Mapping
===========

.. image:: https://badge.fury.io/py/vivarium-gbd-mapping.svg
    :target: https://badge.fury.io/py/vivarium-gbd-mapping

.. image:: https://readthedocs.org/projects/vivarium-gbd-mapping/badge/?version=latest
    :target: https://vivarium-gbd-mapping.readthedocs.io/en/latest/?badge=latest
    :alt: Latest Docs

Mapping of Global Burden of Disease (GBD) entities to their metadata.

The mapping modules are generated from GBD data by a separate, IHME-internal package,
``vivarium-gbd-mapping-generator``, which requires access to the IHME cluster and some of our
internally used data-access libraries. Mapping updates are managed by a maintainer toolchain, so
this shouldn't be an issue for consumers of this package.

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


Development
+++++++++++

Set up this library in development mode with

.. code-block:: bash

   git clone https://github.com/ihmeuw/vivarium-suite.git
   cd vivarium-suite
   pip install -e 'libs/gbd-mapping[dev]'

To regenerate the mapping modules, see the ``vivarium-gbd-mapping-generator`` package.


`Check out the docs! <https://vivarium-gbd-mapping.readthedocs.io/en/latest/>`_
-------------------------------------------------------------------------------
