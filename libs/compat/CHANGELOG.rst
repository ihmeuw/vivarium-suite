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
