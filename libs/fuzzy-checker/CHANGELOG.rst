**0.4.0 - 08/21/26**

**Breaking changes**
- Raise ``ValueError`` when a Bayes factor evaluates to nan

**0.3.0 - 08/06/26**

**Breaking changes**
- ``TargetIntervalConfig`` no longer takes ``stratifications``; it now applies to every
  tested group and exposes an ``applies_to`` hook for subclasses to restrict that
- ``TargetIntervalConfig`` is now keyword-only, so that subclasses can add fields
  without the inherited ``relative_error`` claiming a caller's first positional argument
- Remove ``StratValue`` (which only supported the removed ``stratifications`` field)

**0.2.1 - 07/23/26**

- Streamline package __init__.py docstring
- Add project.urls to pyproject.toml

**0.2.0 - 07/21/26**

- Define classes in ``fuzzy_checker`` and ``data_structures`` modules instead of ``__init__``
- **Breaking change.** Rename ``FuzzyChecker.fuzzy_assert_proportion`` to ``FuzzyChecker.assert_proportion``.

**0.1.0 - 07/20/26**

- Initial release: ``FuzzyChecker`` split out of ``vivarium-testing-utils`` into its own package
- ``TargetIntervalConfig`` and ``StratValue`` now live here alongside ``FuzzyChecker``
