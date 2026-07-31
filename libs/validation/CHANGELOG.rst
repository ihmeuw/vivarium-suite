**0.2.0 - 07/31/26**

- **Breaking change.** Replace ``TargetIntervalConfig`` with
  ``StratifiedTargetIntervalConfig``, which subclasses the now-simplified
  ``vivarium.fuzzy_checker.TargetIntervalConfig`` and owns the ``stratifications``
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
