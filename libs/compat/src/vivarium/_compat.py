"""Backward-compatible import redirects for the vivarium monorepo migration.

Intercepts old-style imports (e.g. ``import layered_config_tree``,
``from vivarium_public_health.disease import DiseaseModel``) and transparently
redirects them to the new ``vivarium.*`` namespace, emitting a DeprecationWarning.

Activated at interpreter startup via ``vivarium_compat.pth`` so the hook is in
place before any user code runs.

To add a redirect when a package migrates, add an entry to ``_REDIRECTS`` and
bump the ``vivarium-compat`` version in ``pyproject.toml``.
"""

import importlib
import importlib.abc
import importlib.machinery
import sys
import warnings
from types import ModuleType

# Old import root -> new import root.
# Activate an entry when its package has been migrated into the monorepo.
# The hook will fail loudly if the new location does not yet exist, so do not
# enable an entry before its target package is released.
_REDIRECTS: dict[str, str] = {
    # Renamed top-level packages (enable when each package migrates)
    # "vivarium_profiling": "vivarium.profiling",
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
    # "layered_config_tree": "vivarium.config_tree",
}


def _match(fullname: str) -> tuple[str, str] | None:
    """Return (old_prefix, new_prefix) if fullname matches a redirect, else None."""
    for old, new in _REDIRECTS.items():
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

        if fullname in sys.modules:
            # Already loaded under the old name — return a spec that resolves to it.
            return importlib.machinery.ModuleSpec(fullname, _CompatLoader(fullname, new_name))

        warnings.warn(
            f"'{fullname}' has moved to '{new_name}'. "
            "Update your imports. This redirect will be removed in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
        return importlib.machinery.ModuleSpec(fullname, _CompatLoader(fullname, new_name))


class _CompatLoader(importlib.abc.Loader):
    def __init__(self, old_name: str, new_name: str) -> None:
        self._old_name = old_name
        self._new_name = new_name

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        return None  # Use default semantics.

    def exec_module(self, module: ModuleType) -> None:
        # Import (or retrieve) the real module at the new location.
        real = importlib.import_module(self._new_name)
        # Make the old name an alias so that subsequent imports are instant.
        sys.modules[self._old_name] = real
        # Copy the real module's attributes onto the placeholder module object
        # in case anyone holds a direct reference to it.
        module.__dict__.update(real.__dict__)
        module.__spec__ = real.__spec__


def install() -> None:
    """Install the compat finder into sys.meta_path (idempotent)."""
    if not any(isinstance(f, _CompatFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _CompatFinder())
