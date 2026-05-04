# vivarium-compat

Backward-compatible import redirects for the vivarium monorepo migration.

**Temporary** - remove once all downstream packages have migrated to the new import paths.

## How it works

The package has three layers:

**1. Bootstrap (`vivarium_compat.pth`)**

Installed to `site-packages` root. Python's `site` module executes every `.pth` file 
at interpreter startup, before any user code runs. This file calls `vivarium._compat.install()`,
inserting the import hook into `sys.meta_path`.

**2. Import machinery (`sys.meta_path`)**

For every `import` statement, Python walks `sys.meta_path` in order and asks each
finder if it knows how to load the module. By inserting `_CompatFinder` at position
0, it gets first crack at every import.

**3. Redirect (`_CompatFinder` + `_CompatLoader`)**

`find_spec` checks whether the imported name matches an entry in `_REDIRECTS`. On
a match it emits a `DeprecationWarning` and returns a spec backed by `_CompatLoader`.
`exec_module` then loads the real module at the new path, registers it under the
old name in `sys.modules`, and copies its attributes onto the placeholder so both
the old and new names resolve to the same object.

## Adding a redirect

When a package migrates into the monorepo, uncomment its entry in `_REDIRECTS` in
`src/vivarium/_compat.py` and bump the patch version in `pyproject.toml`:

```python
_REDIRECTS: dict[str, str] = {
    "layered_config_tree": "vivarium.config_tree",  # uncomment when libs/config-tree/ ships
    ...
}
```

Do not enable an entry before its target package is released; the hook will raise
`ModuleNotFoundError` loudly if the new location doesn't exist.

## Removal

Once all downstream packages have released versions using the new import paths:

1. Delete `libs/_compat/`
2. Remove `vivarium-compat` from any `dev` dependencies that reference it
3. Remove the row from the root `README.md`
