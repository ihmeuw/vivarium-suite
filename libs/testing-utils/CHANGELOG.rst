**0.7.3 - 07/06/26**

- Update ``vivarium-inputs`` pin to ``>=8.0.0`` for the ``gbd_mapping`` monorepo migration

**0.7.2 - 07/02/26**

- Write a per-xdist-worker file in ``FuzzyChecker.save_diagnostic_output``
- Copy ``MEASURE_KEY_MAPPINGS`` inner dicts in ``MeasureMapper`` to avoid shared-state mutation

**0.7.1 - 06/30/26**

- Add support for 'make build-env' without access to the IHME Artifactory.

**0.7.0 - 06/29/26**

- Update ``-n auto`` to pick a resource-aware worker count

**0.6.2 - 06/22/26**

- Pin vivarium-build-utils to v4.x and update Makefile to use ``vivarium.build_utils``

**0.6.1 - 06/17/26**

- Switch Jenkins ``vivarium_build_utils`` shared library loading from the ``epic/monorepo``
  branch to the version returned by ``get_vbu_version()``

**0.6.0 - 06/11/26**

Initial release from the vivarium-suite monorepo; the standalone ``ihmeuw/vivarium_testing_utils``
repository has been archived.

Breaking changes:

- Import path changed from ``vivarium_testing_utils`` to ``vivarium.testing_utils``.
- Sibling imports used inside the ``automated_validation`` feature changed to monorepo paths (``vivarium.<pkg>``)
- ``__about__`` attributes (``__author__``, ``__copyright__``, ``__email__``,
  ``__license__``, ``__summary__``, ``__title__``, ``__uri__``) are no longer
  exposed on ``vivarium.testing_utils``. Use ``importlib.metadata.metadata("vivarium-testing-utils")``
  for the equivalent values.
- Heavier optional deps (``pyarrow``, ``seaborn``) moved into the ``validation``
  extra alongside the rest of the validation feature; base installs no longer
  pull them in.

**0.5.4 - 05/06/26**

  - Feature: Add option to manually run "weekly" tests

**0.5.3 - 05/05/26**

  - Bugfix: Only skip for explicit marker calls with pytest plugin

**0.5.2 - 04/16/26**

  - Tighten vivarium_build_utils pin

**0.5.1 - 04/15/26**

  - Update vivarium_build_utils pin

**0.5.0 - 03/31/26**

  - Feature: Add pytest-xdist auto-worker detection to pytest plugin.
    Repos opt in by adding ``addopts = "-nauto"`` to ``pyproject.toml``.

**0.4.0 - 03/27/26**

  - Phase 2 Automated Validation
    - refactor FuzzyChecker so fuzzy checks can be done on dataframes
    - adds TestResult dataclass to capture results of fuzzy checks
    - Use FuzzyChecker to validate Comparisons, using verify and verify_all methods on ValidationContext
    - Generate user html report with results and plots for each comparison

**0.3.6 - 03/16/26**

  - Validate version prior to deploying
  - Bugfix: Update intersphinx mapping for python and pandas

**0.3.5 - 02/23/26**

  - Feature: Allow VTU to be installed as a package with python 3.12 and 3.13

**0.3.4 - 02/20/26**

  - Feature: Add pytest options and configuration details to pytest plugin

**0.3.3 - 02/06/26**

  - Feature: create a pytest plugin with extraction of "no_gbd_cache"

**0.3.2 - 01/26/26**

  - Feature: Update AgeGroup and AgeGroupSchema to handle subsets

**0.3.1 - 01/06/26**

  - Fail deployment if changelog date does not match current date

**0.3.0 - 12/12/25**

  - Phase 1 Automated Validation, ValidationContext component for simulation validation

**0.2.6 - 11/20/25**

  - Improve 'make build-env': better handle args and make the env name optional

**0.2.5 - 08/01/25**

  - Use vivarium_dependencies for common setup constraints

**0.2.4 - 07/25/25**

  - Feature: Support new environment creation via 'make build-env'

**0.2.3 - 07/16/25**

  - Support pinning of vivarium_build_utils; pin vivarium_build_utils>=1.1.0,<2.0.0

**0.2.2 - 05/27/25**

  - Update pandas stubs package pin

**0.2.1 - 02/05/24**

  - Add python versions json

**0.2.0 - 11/21/24**

  - Drop support for Python 3.9

**0.1.2 - 10/31/24**

  - Add mypy type checking
  - Add unit tests for FuzzyChecker

**0.1.1 - 10/14/24**

  - Make name an optional parameter to fuzzy_assert_proportion

**0.1.0 - 03/01/24**

  - Repository creation
