# vivarium-compat (retired)

> **This package is retired as of version 1.0.0.** It no longer does
> anything at runtime; the `.pth` file is a comment-only no-op and the
> import-redirect hook has been deleted.
>
> If you have `vivarium-compat` in your project dependencies, remove
> it. If your code still relies on the old top-level import paths this
> package used to redirect, migrate them:
>
> | Old import | New import |
> |---|---|
> | `import vivarium_profiling` | `import vivarium.profiling` |
> | `import risk_distributions` | `import vivarium.risk_distributions` |
> | `import gbd_mapping` | `import vivarium.gbd_mapping` |
> | `import gbd_mapping_generator` | `import vivarium.gbd_mapping_generator` |
>
> See [MIC-7100](https://jira.ihme.washington.edu/browse/MIC-7100) for
> context. The source directory `libs/compat/` will be removed from the
> vivarium-suite monorepo shortly after this final release lands on
> PyPI.

## History

vivarium-compat existed as a transitional artifact for the vivarium
monorepo migration. It installed a `.pth`-based import hook at Python
startup that transparently redirected legacy top-level imports to their
new `vivarium.<subpkg>` locations.

The team decided in 2026-07 to abandon the backwards-compatibility
strategy and let downstream code adapt directly. All in-monorepo callers
have been migrated. This is the final release; no further updates.
