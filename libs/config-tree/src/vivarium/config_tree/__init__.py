"""
====================
Vivarium Config Tree
====================

A configuration structure supporting cascading layers.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("vivarium-config-tree")
except PackageNotFoundError:
    __version__ = "0.0.0+not-installed"

from vivarium.config_tree.exceptions import (
    ConfigurationError,
    ConfigurationKeyError,
    DuplicatedConfigurationError,
    ImproperAccessError,
    MissingLayerError,
)
from vivarium.config_tree.main import ConfigNode, ConfigTree, load_yaml
