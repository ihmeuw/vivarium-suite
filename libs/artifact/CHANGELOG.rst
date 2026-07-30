**1.0.9 - 07/30/26**

- Make the ``hdf`` module functions private

**1.0.8 - 07/23/26**

- Streamline package __init__.py docstring
- Add project.urls to pyproject.toml

**1.0.7 - 07/14/26**

- Update stale references for monorepo libraries

**1.0.6 - 07/06/26**

- Add PyPI classifiers

**1.0.5 - 06/30/26**

- Add support for 'make build-env' without access to the IHME Artifactory.

**1.0.4 - 06/22/26**

- Pin vivarium-build-utils to v4.x and update Makefile to use ``vivarium.build_utils``

**1.0.3 - 06/17/26**

- Switch Jenkins ``vivarium_build_utils`` shared library loading from the ``epic/monorepo``
  branch to the version returned by ``get_vbu_version()``

**1.0.2 - 06/08/26**

- Extract ``EntityKey`` into its own module and export ``is_entity_key`` helper

**1.0.1 - 06/01/26**

- Migrate the Artifact tutorial from vivarium (engine) docs
- Fix doc references to old vivarium
- Replace stale nitpick exception

**1.0.0 - 05/20/26**

Initial release as a standalone package extracted from ``vivarium``. Provides
the data-artifact model (``Artifact``, ``ArtifactException``), the ``EntityKey``
address type, and the HDF5 read/write/load/remove free functions.
