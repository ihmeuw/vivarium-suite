**0.2.0 - 07/31/26**

- **Breaking change.** ``TargetIntervalConfig`` no longer accepts
  ``stratifications``. It is still importable from ``vivarium.validation.comparison``,
  but callers that filter by stratification must switch to the new
  ``StratifiedTargetIntervalConfig``, which subclasses it and owns that filtering
- **Breaking change.** ``StratifiedTargetIntervalConfig`` is keyword-only; the old
  class could be constructed positionally
- Define ``StratValue`` here instead of re-exporting it from ``vivarium-fuzzy-checker``

**0.1.2 - 07/23/26**

- Streamline package __init__.py docstring
- Add project.urls to pyproject.toml

**0.1.1 - 07/22/26**

- Bugfix: reorganize tests into subdirs to fix a test suite collection error

**0.1.0 - 07/21/26**

- Initial release: automated verification and validation (V&V) tooling split out
  of ``vivarium-testing-utils`` into its own package.
