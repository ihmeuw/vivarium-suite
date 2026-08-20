**0.3.0 - 08/21/26**

**Breaking changes**
- Require ``vivarium-fuzzy-checker>=0.4.0``, which now raises on a nan Bayes factor
  instead of reporting the test as a confident pass
- Convert person-time to a whole number of person-steps and annual rates to a
  per-time-step probability before fuzzy checking, rather than dividing the target by
  the step size, which inflated it by a factor of 1/step_size
- ``verify()`` now raises ``ValueError`` when no step size is given for a measure
  recorded in person-time, which is every measure in this package, rather than silently
  skipping the conversion
- Scale only rate measures' targets by the step size, which fixes ``prevalence``, and
  defer to the affected measure for ``relative_risk``, whose reference is a rate exactly
  when the affected measure's data is
- Set ``measure`` on ``RiskStatePersonTime`` so risk exposure data is recognized as
  person-time

**0.2.1 - 08/06/26**

- Support pandas 3

**0.2.0 - 08/06/26**

- **Breaking change.** ``set_target_interval`` now builds a ``StratifiedTargetIntervalConfig``,
  a subclass ``vivarium.fuzzy_checker.TargetIntervalConfig`` that owns the ``stratifications``
  filtering
- Define ``StratValue`` here instead of re-exporting it from ``vivarium-fuzzy-checker``

**0.1.2 - 07/23/26**

- Streamline package __init__.py docstring
- Add project.urls to pyproject.toml

**0.1.1 - 07/22/26**

- Bugfix: reorganize tests into subdirs to fix a test suite collection error

**0.1.0 - 07/21/26**

- Initial release: automated verification and validation (V&V) tooling split out
  of ``vivarium-testing-utils`` into its own package.
