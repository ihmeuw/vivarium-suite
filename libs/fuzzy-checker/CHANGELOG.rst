**0.2.1 - 07/23/26**

- Run pandas >=2.1 test suites with copy-on-write and ``future.infer_string``
  enabled to exercise pandas 3 semantics ahead of the unpin (MIC-6773)

**0.2.0 - 07/21/26**

- Define classes in ``fuzzy_checker`` and ``data_structures`` modules instead of ``__init__``
- **Breaking change.** Rename ``FuzzyChecker.fuzzy_assert_proportion`` to ``FuzzyChecker.assert_proportion``.

**0.1.0 - 07/20/26**

- Initial release: ``FuzzyChecker`` split out of ``vivarium-testing-utils`` into its own package
- ``TargetIntervalConfig`` and ``StratValue`` now live here alongside ``FuzzyChecker``
