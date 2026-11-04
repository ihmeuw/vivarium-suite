**2.0.0 - 11/04/26**

- **Breaking change.** Require pandas 3 and numpy 2.

  - ``pandas>=3.0.0``: the suite supports pandas 3, and pandas 3 is the
    oldest version it tests.
  - ``numpy>=2.1.0``: pyarrow requires numpy 2 at import time, and numpy
    2.1.0 is the first release with Python 3.13 wheels.

- **Breaking change.** Remove the ``numpy_lt_2`` extra. No package in the
  monorepo uses it, and numpy 1 does not work with the ``pyarrow`` extra.
- Add a lower bound to the other extras of the core scientific stack. For the
  compiled libraries, the bound is the oldest release with wheels for every
  supported Python version (3.11 to 3.13).

  - ``pyarrow>=18.0.0``: the first release with Python 3.13 wheels. It also
    supports numpy 2 and is above the pyarrow 13 minimum of pandas 3.
  - ``scipy>=1.14.1``: the first release with Python 3.13 wheels.
  - ``tables>=3.10.1``: the first release with Python 3.13 wheels. It also
    supports numpy 2.
  - ``matplotlib>=3.9.2``: the first release with Python 3.13 wheels. It also
    supports numpy 2.
  - ``click>=8.4.0``: vivarium-cluster-tools subclasses ``click.ParamType[...]``,
    and ``ParamType`` is generic only from click 8.4.0.
  - ``loguru>=0.7.2``: the vivarium-cluster-tools task runner tests fail with
    loguru 0.7.0 and 0.7.1.
  - ``networkx>=3.0``: networkx is pure Python, and the suite passes its tests
    with networkx 3.0.

- Add a ``pandera`` extra with ``pandera[pandas]>=0.31.0``. It also includes the
  ``pandas`` extra, so it has the pandas floor. vivarium-validation tests
  pandera 0.31.0 with pandas 3.

**1.3.1 - 09/22/26**

- Fail fast when ``make build-env`` fails and tear down the partially-built
  environment, unless ``keep_env=yes`` is passed.

**1.3.0 - 08/25/26**

- **Breaking change.** Drop support for Python 3.10 and add support for Python 3.11 and 3.12

**1.2.4 - 07/14/26**

- Update stale references for monorepo libraries

**1.2.3 - 06/30/26**

- Add support for 'make build-env' without access to the IHME Artifactory.

**1.2.2 - 06/30/26**

- Add pytest-xdist to pytest group

**1.2.1 - 06/22/26**

- Pin vivarium-build-utils to v4.x and update Makefile to use ``vivarium.build_utils``

**1.2.0 - 06/17/26**

- Remove unused vivarium-build-utils pin definition

**1.1.0 - 06/12/26**

Initial release from the vivarium-suite monorepo; the standalone ``ihmeuw/vivarium_dependencies``
repository has been archived.

- Replaced ``setup.py`` with a PEP 621 ``pyproject.toml``
- Renamed ``LICENSE.txt`` to ``LICENSE`` to match the monorepo convention
- Scrubbed now-unnecessary scaffolding files

**1.0.8 - 06/09/26**

- Replace networkx-stubs with types-networkx in the networkx extra

**1.0.7 - 04/14/26**

- Add vbu pin

**1.0.6 - 03/16/26**

- Validate version and CHANGELOG date prior to deploying

**1.0.5 - 01/21/26**

- Pin pandas<3.0.0

**1.0.4 - 1/13/26**

- Pin scipy<1.17.0

**1.0.3 - 1/12/26**

- Pin sphinx<9.0.0

**1.0.2 - 12/10/25**

- Add dependency: sphinx-rtd-theme>=2.0.0

**1.0.1 - 8/1/25**

- Add dependency: numpy<2.0.0

**1.0.0 - 7/31/25**

- Initial release

**0.1.0 - 7/29/25**

- Initial release candidate
