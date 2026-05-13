"""Backward-compatible import redirects for the vivarium monorepo migration.

Intercepts old-style imports (e.g. ``import layered_config_tree``,
``from vivarium_public_health.disease import DiseaseModel``) and transparently
redirects them to the new ``vivarium.*`` namespace, emitting a DeprecationWarning.

Activated at interpreter startup via ``vivarium_compat.pth`` so the hook is in
place before any user code runs.

To add a redirect when a package migrates, add an entry to ``_REDIRECTS`` and
bump the ``vivarium-compat`` version in ``pyproject.toml``. Entries are safe
to add *before* the target package is released: if the new module isn't
importable, the hook falls back to letting the old on-disk package resolve
normally, so installations during the transition window aren't broken.

Remove this module once all downstream packages have released versions that use
the new import paths and the deprecation period has ended.
"""

import importlib
import importlib.abc
import importlib.machinery
import sys
import warnings
from types import ModuleType

# Old import root -> new import root.
# Entries here are safe to populate ahead of the target package's release:
# if the new target isn't installed yet, the hook falls back to the old
# package's normal on-disk location (so existing installations during the
# transition window keep working).
_REDIRECTS: dict[str, str] = {
    # Renamed top-level packages
    "vivarium_profiling": "vivarium.profiling",
    "layered_config_tree": "vivarium.config_tree",
    # "vivarium_public_health": "vivarium.public_health",
    # "vivarium_cluster_tools": "vivarium.cluster_tools",
    # "vivarium_testing_utils": "vivarium.testing_utils",
    # "vivarium.examples": "vivarium.core.examples",
    # "vivarium.framework": "vivarium.core.framework",
    # "vivarium.interface": "vivarium.core.interface",
    # "vivarium.component": "vivarium.core.component",
    # "vivarium.exceptions": "vivarium.core.exceptions",
    # "vivarium.manager": "vivarium.core.manager",
    # "vivarium.testing_utilities": "vivarium.core.testing_utilities",
    # "vivarium.types": "vivarium.core.types",
    # "vivarium_helpers": "vivarium.helpers",
    # "risk_distributions": "vivarium.risk_distributions",
    # "gbd_mapping": "vivarium.gbd_mapping",
    "layered_config_tree": "vivarium.config_tree",
}

# Tracks which old names are currently being resolved to prevent infinite
# recursion if a redirect target somehow re-triggers the same old-name import.
_resolving: set[str] = set()


def _match(fullname: str) -> tuple[str, str] | None:
    """Return (old_prefix, new_prefix) if fullname matches a redirect, else None.

    Longer prefixes take precedence so more-specific entries win over broader ones.
    """
    for old, new in sorted(_REDIRECTS.items(), key=lambda x: -len(x[0])):
        if fullname == old or fullname.startswith(old + "."):
            return old, new
    return None


class _CompatFinder(importlib.abc.MetaPathFinder):
    """Meta-path finder that redirects deprecated import paths to new locations."""

    def find_spec(
        self,
        fullname: str,
        path: object,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        match = _match(fullname)
        if match is None:
            return None

        old_prefix, new_prefix = match
        new_name = new_prefix + fullname[len(old_prefix) :]

        # Only warn on first import. After exec_module runs, sys.modules[fullname]
        # is set to the real module, so subsequent imports return it directly without
        # ever reaching find_spec again. This branch fires only in unusual re-entry
        # scenarios (e.g. reload()).
        if fullname not in sys.modules:
            # stacklevel=2 points into importlib internals rather than the caller's
            # import statement - the exact depth is CPython-version-dependent.
            # The warning message itself is the actionable part.
            warnings.warn(
                f"'{fullname}' has moved to '{new_name}'. "
                "Update your imports. This redirect will be removed in a future release.",
                DeprecationWarning,
                stacklevel=2,
            )
        # submodule_search_locations is intentionally not set on the spec. CPython's
        # import machinery calls exec_module before checking __path__ on any child import,
        # and exec_module replaces sys.modules[fullname] with the real module (which already
        # has the correct __path__). Setting it to [] would be a no-op at best and misleading
        # at worst since we don't know at spec-creation time whether the target is a package.
        return importlib.machinery.ModuleSpec(
            fullname, _CompatLoader(fullname, new_name)
        )


class _CompatLoader(importlib.abc.Loader):
    """Loads the real module at the new location and aliases it under the old name."""

    def __init__(self, old_name: str, new_name: str) -> None:
        self._old_name = old_name
        self._new_name = new_name

    def exec_module(self, module: ModuleType) -> None:
        if self._old_name in _resolving:
            raise ImportError(
                f"Circular redirect detected: '{self._old_name}' -> '{self._new_name}'"
            )
        _resolving.add(self._old_name)
        try:
            try:
                real = importlib.import_module(self._new_name)
            except ModuleNotFoundError:
                # FIXME: MIC-7100 Revert when all packages are migrated
                # New target isn't installed. Fall back to the old name's actual
                # on-disk location so installations during the transition window
                # — old package still installed, new package not yet released —
                # keep working. The DeprecationWarning was already emitted in
                # find_spec(); users will see it whether or not the fallback fires.
                real = _import_bypassing_compat(self._old_name)
            # Register under the old name so subsequent imports hit sys.modules directly.
            sys.modules[self._old_name] = real
            # Defensive: covers the case where another import hook holds a direct
            # reference to the placeholder module object rather than re-reading sys.modules.
            module.__dict__.update(real.__dict__)
            # __spec__.name will show the new name (e.g. "vivarium.config_tree"), not the
            # old one. This is intentional: sys.modules[old_name] IS real, so its metadata
            # correctly describes its actual location. Users inspecting __spec__ after
            # migrating their imports will see the right thing.
            module.__spec__ = real.__spec__
        finally:
            _resolving.discard(self._old_name)


def _import_bypassing_compat(name: str) -> ModuleType:
    """Import `name` without going through our compat finder.

    Used to fall back to a name's real on-disk location when the redirect target
    isn't installed. Temporarily removes our finder from sys.meta_path so the
    standard PathFinder resolves the name directly. Also clears any placeholder
    sys.modules entry the import system set when our find_spec returned a spec.
    """
    sys.modules.pop(name, None)
    saved: list[tuple[int, _CompatFinder]] = [
        (i, f) for i, f in enumerate(sys.meta_path) if isinstance(f, _CompatFinder)
    ]
    for _, f in saved:
        sys.meta_path.remove(f)
    try:
        return importlib.import_module(name)
    finally:
        # Reinsert in original order so any other meta_path entries stay in place.
        for i, f in saved:
            sys.meta_path.insert(i, f)


def install_compat_finder() -> None:
    """Install the compat finder into sys.meta_path (idempotent)."""
    if not any(isinstance(f, _CompatFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _CompatFinder())
