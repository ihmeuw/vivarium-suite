"""Config Tree: a configuration structure supporting cascading layers."""

import warnings
from importlib.metadata import PackageNotFoundError, version
from typing import Any

try:
    __version__ = version("vivarium-config-tree")
except PackageNotFoundError:
    __version__ = "unknown"

from vivarium.config_tree.exceptions import (
    ConfigurationError,
    ConfigurationKeyError,
    DuplicatedConfigurationError,
    ImproperAccessError,
    MissingLayerError,
)
from vivarium.config_tree.main import ConfigNode, ConfigTree, load_yaml


def __getattr__(name: str) -> Any:
    """Module-level ``__getattr__`` providing a deprecation alias for the old class name.

    ``LayeredConfigTree`` was renamed to ``ConfigTree``. Importing the old name still
    works (returns ``ConfigTree``) but emits a ``DeprecationWarning``. Remove this
    shim once downstream callers have migrated.
    """
    if name == "LayeredConfigTree":
        warnings.warn(
            "'LayeredConfigTree' has been renamed to 'ConfigTree'. "
            "Update your imports. This alias will be removed in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
        return ConfigTree
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
