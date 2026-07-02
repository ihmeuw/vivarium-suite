**1.0.0 - 07/02/26**

**Final release.** vivarium-compat is retired.

- Gut the import-redirect hook: ``vivarium_compat.pth`` is now a
  comment-only no-op and ``vivarium_compat/_compat.py`` has been
  removed. Installing this version of vivarium-compat does *nothing*
  at Python startup - no ``sys.meta_path`` entries are added, no
  legacy imports get redirected.
- Rewrite ``vivarium_compat/__init__.py`` and ``README.md`` as
  deprecation banners pointing users at the new ``vivarium.<subpkg>``
  import paths.
- Delete the package's own tests (``libs/compat/tests/``): they
  validated behavior that no longer exists.

Migrate any remaining ``import <old_name>`` statements in your code to
``import vivarium.<new_name>``. See MIC-7100 for the broader context.
This is the last release from this package. The source directory
``libs/compat/`` will be removed from the vivarium-suite monorepo
immediately after this release lands on PyPI. Please remove
``vivarium-compat`` from your project dependencies.

**0.6.4 - 06/30/26**

- Add support for 'make build-env' without access to the IHME Artifactory.

**0.6.3 - 06/22/26**

- Pin vivarium-build-utils to v4.x and update Makefile to use ``vivarium.build_utils``

**0.6.2 - 06/17/26**

- Switch Jenkins ``vivarium_build_utils`` shared library loading from the ``epic/monorepo``
  branch to the version returned by ``get_vbu_version()``

**0.6.1 - 05/22/26**

- Remove the layered_config_tree and vivarium redirects (temporarily)

**0.6.0 - 05/21/26**

- Add vivarium.<module> -> vivarium-engine.<module> redirects for top level modules.
- Add ``layered_config_tree`` -> ``vivarium.config_tree`` redirect
  (previously disabled pending the vivarium migration).

**0.5.2 - 05/20/26**

- Tighten tag pattern

**0.5.1 - 05/19/26**

- add license and authors to pyproject.toml
- modify fallback version

**0.5.0 - 05/19/26**

- Add gbd_mapping and gbd_mapping_generator redirects
- Add LICENSE file
- Add package-level tests (test_package.py)

**0.4.0 - 05/18/26**

- Add risk_distributions redirect

**0.3.1 - 05/15/26**

- Disable layered_config_tree redirect

**0.3.0 - 05/14/26**

- BREAKING (packaging only): move the compat module out of the ``vivarium``
  namespace to a top-level package ``vivarium_compat``. The hook's
  ``vivarium_compat.pth`` startup file previously imported
  ``vivarium._compat._compat``, which forced Python to run
  ``vivarium/__init__.py`` before the redirect hook was installed. If the
  vivarium package's ``__init__`` imported anything that depended on the
  hook (e.g. ``from layered_config_tree import ...`` once the v4.1.8 shim
  was installed), the .pth load deadlocked and every Python interpreter
  startup in that env printed a ``ModuleNotFoundError``. The hook now lives
  at ``vivarium_compat._compat`` so .pth load no longer touches the
  vivarium package. No user-visible API change since downstream packages
  don't import compat directly.

**0.2.1 - 05/14/26**

- Update test_removal_deadline to reflect new removal deadline of 2027-07-01.

**0.2.0 - 05/13/26**

- Activate ``layered_config_tree`` -> ``vivarium.config_tree`` redirect.
- Make the import hook degrade gracefully: if a redirect's target package
  isn't installed (e.g. during the transition window where the old standalone
  is still on disk but the new monorepo package hasn't been released yet),
  fall back to the old name's normal on-disk location instead of raising
  ``ModuleNotFoundError``. The ``DeprecationWarning`` still fires.

**0.1.5- 05/13/26**

- Add vivarium_testing_utils to test requirements for --runslow plugin

**0.1.4 - 05/08/26**

- Add github and jenkins CI dependencies

**0.1.3 - 05/08/26**

- Remove unnecessary init file from vivarium._compat subpackage.

**0.1.2 - 05/07/26**

- Add vivarium_profiling redirect

**0.1.1 - 05/07/26**

- Add Jenkinsfile for overnight builds
- Rename ``libs/_compat/`` to ``libs/compat/`` to match the public PyPI distribution name.


**0.1.0 - 05/07/26**

- Initial release. Provides import-redirect shim for the vivarium monorepo migration.
