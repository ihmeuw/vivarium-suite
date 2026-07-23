**0.1.2 - 07/23/26**

- Support pandas 3 (MIC-6773): version-gate the NaN-preserving ``stack()`` kwargs
  (``dropna=`` was removed in pandas 3), pass ``observed=True`` when grouping plot
  data, make ``CategoricalRelativeRisk`` index level order deterministic across
  pandas versions, and run pandas >=2.1 test suites with copy-on-write and
  ``future.infer_string`` enabled; suite verified green on pandas 1.5.3, 2.3.3,
  and 3.0.3

**0.1.1 - 07/22/26**

- Bugfix: reorganize tests into subdirs to fix a test suite collection error

**0.1.0 - 07/21/26**

- Initial release: automated verification and validation (V&V) tooling split out
  of ``vivarium-testing-utils`` into its own package.
