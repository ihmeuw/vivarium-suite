**0.3.0 - 08/25/26**

- Fix a bug in converting rates to probabilities for fuzzy checking
- Require ``vivarium-fuzzy-checker>=0.4.0`` and add a ``vivarium-engine`` dependency,
  which supplies the rate conversion
- ``verify()`` takes the rate conversion type, raises without a step size or on a rate
  too high to express as a probability, and no longer reports a comparison as verified
  when any of its tests did not evaluate

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
