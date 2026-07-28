**0.1.3 - 07/24/26**

- Support pandas 3 (MIC-6773): version-gate the NaN-preserving ``stack()`` kwargs
  (specifying ``dropna=`` raises under pandas 3's default ``future_stack=True``
  implementation), pass ``observed=True`` when grouping plot data, and make
  ``CategoricalRelativeRisk`` index level order deterministic across pandas
  versions

**0.1.2 - 07/23/26**

- Streamline package __init__.py docstring
- Add project.urls to pyproject.toml

**0.1.1 - 07/22/26**

- Bugfix: reorganize tests into subdirs to fix a test suite collection error

**0.1.0 - 07/21/26**

- Initial release: automated verification and validation (V&V) tooling split out
  of ``vivarium-testing-utils`` into its own package.
