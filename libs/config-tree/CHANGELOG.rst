**5.2.0 - 08/26/26**

- **Behavior change.** ``ConfigTree.get`` returns ``default_value`` for a missing key anywhere in
  the key path (previously ``ConfigurationKeyError``) and for a prefix key resolving to a value
  rather than a sub-tree (previously ``ConfigurationError``). Use ``get_tree`` for a strict lookup.
- Stop ``ConfigTree.get`` from mutating the ``keys`` argument

**5.1.0 - 08/25/26**

- **Breaking change.** Drop support for Python 3.10

**5.0.12 - 07/23/26**

- Streamline package __init__.py docstring
- Add project.urls to pyproject.toml

**5.0.11 - 07/14/26**

- Update stale references for monorepo libraries

**5.0.10 - 07/14/26**

**Breaking change.** Remove the ``LayeredConfigTree`` deprecation alias that has
been re-exported from ``vivarium.config_tree`` since the monorepo migration. Callers
must now use the canonical name ``ConfigTree``.

- Delete the module-level ``__getattr__`` in ``vivarium/config_tree/__init__.py``.
- Delete related deprecation test cases.

**5.0.9 - 07/06/26**

- Add PyPI classifiers

**5.0.8 - 06/30/26**

- Add support for 'make build-env' without access to the IHME Artifactory.

**5.0.7 - 06/22/26**

- Pin vivarium-build-utils to v4.x and update Makefile to use ``vivarium.build_utils``

**5.0.6 - 06/17/26**

- Switch Jenkins ``vivarium_build_utils`` shared library loading from the ``epic/monorepo``
  branch to the version returned by ``get_vbu_version()``

**5.0.5 - 05/20/26**

- Tighten tag pattern
- Tighten setuptools include pattern (avoid matching hypothetical sibling packages)
- Use style.css for docs
- Calculate current year more robustly in docs/conf.py

**5.0.4 - 05/19/26**

- Update LICENSE file
- Remove .gitignore file
- Add package-level tests (test_package.py)

**5.0.3 - 05/18/26**

- Update init fallback version
- Add explicit 'lint' optional dependency
- Remove unused sys.path insert in docs conf.py
- Add clarifying comment to readthedocs.yaml

**5.0.2 - 05/15/26**

- Bugfix: point to correct readthedocs files

**5.0.1 - 05/15/26**

- Drop vivarium-compat runtime dependency

**5.0.0 - 05/14/26**

Initial release from the vivarium-suite monorepo; the standalone ``layered_config_tree``
repository has been archived.

Breaking changes:
- PyPI distribution renamed from ``layered_config_tree`` to ``vivarium-config-tree``.
- Import path changed from ``layered_config_tree`` to ``vivarium.config_tree``.
- The primary class ``LayeredConfigTree`` has been renamed to ``ConfigTree``.

**4.1.7 - 05/11/26**

- Type hint: Remove unused ignore

**4.1.6 - 04/16/26**

- Tighten vivarium_build_utils pin

**4.1.5 - 04/15/26**

- Update vivarium_build_utils pin

**4.1.4 - 04/14/26**

- Go back to explicit vbu pin (revert change from v4.1.3)

**4.1.3 - 04/14/26** (YANKED)

- Use vivarium_dependencies for vbu pins

**4.1.2 - 04/14/26**

- Strengthen documentation

**4.1.1 - 03/16/26**

- Validate version and CHANGELOG date prior to deploying
- Update python docs url

**4.1.0 - 02/19/26**

- Add support for Python versions 3.12 and 3.13

**4.0.7 - 01/06/26**

- Fail deployment if changelog date does not match current date

**4.0.6 - 11/20/25**

- Improve 'make build-env': better handle args and make the env name optional

**4.0.5 - 11/12/2025**

- Add API docs and a getting started guide

**4.0.4 - 08/04/2025**

- Remove deprecated reusable_pipeline 'use_shared_fs' arg from Jenkinsfile

**4.0.3 - 08/01/2025**

- Use vivarium_dependencies for common setup constraints

**4.0.2 - 07/25/2025**

- Feature: Support new environment creation via 'make build-env'

**4.0.1 - 07/16/2025**

- Support pinning of vivarium_build_utils; pin vivarium_build_utils>=1.1.0,<2.0.0

**4.0.0 - 07/03/2025**

- Remove get_from_layer() method

**3.2.0 - 04/03/2025**

- Bugfix: Raise a MissingLayerError if a requested value exists but not at the requested layer.
- Get nested values from a single 'get' or 'get_tree' call
- Move tree.get_from_layer() logic into tree.get() and add deprecation warning. 
- Utilize centralized build tools

**3.1.0 - 03/18/2025**

- Raise an error if YAML contains duplicate keys within the same level

**3.0.0 - 02/18/2025**

- Better handle dunder-style keys

**2.2.1 - 12/27/2024**

- Bugfix: failing mypy

**2.2.0 - 11/21/2024**

- Drop support for Python 3.9

**2.1.0 - 10/31/2024**

- Add getter methods

**2.0.2 - 08/01/2024**

- Create explicit iterator for LayeredConfigTree

**2.0.1 - 06/14/2024**

- Add py.typed marker

**2.0.0 - 05/17/2024**

- Drop support for Python v3.8
- Add type hints

**1.0.2 - 04/26/2024**

- Allow default None argument for ConfigurationError

**1.0.1 - 04/11/2024**

- Extract python version test matrix from python_versions.json
- Automatically update README when supported python versions change
- Bugfix missing ConfigurationError attribute

**1.0.0 - 04/11/2024**

- Initial release
