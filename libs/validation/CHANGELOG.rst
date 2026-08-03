**0.2.0 - 07/31/26**

- **Breaking change.** ``set_target_interval`` now builds a
  ``StratifiedTargetIntervalConfig``, a subclass of the now-simplified
  ``vivarium.fuzzy_checker.TargetIntervalConfig`` that owns the ``stratifications``
  filtering. The base class is still re-exported from ``vivarium.validation.comparison``
  and still accepted wherever a target interval config is set
- ``StratifiedTargetIntervalConfig`` is keyword-only. Dataclass inheritance would
  otherwise make ``stratifications`` a leading positional parameter, ahead of the
  inherited ``relative_error`` and so the reverse of the old combined class's order;
  rejecting positional construction turns a silent mis-binding into a clear error
- Define ``StratValue`` here instead of re-exporting it from ``vivarium-fuzzy-checker``

**0.1.2 - 07/23/26**

- Streamline package __init__.py docstring
- Add project.urls to pyproject.toml

**0.1.1 - 07/22/26**

- Bugfix: reorganize tests into subdirs to fix a test suite collection error

**0.1.0 - 07/21/26**

- Initial release: automated verification and validation (V&V) tooling split out
  of ``vivarium-testing-utils`` into its own package.
