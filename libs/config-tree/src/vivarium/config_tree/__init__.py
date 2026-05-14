"""Layered Config Tree: a configuration structure supporting cascading layers."""

from importlib.metadata import PackageNotFoundError, version

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
from vivarium.config_tree.main import ConfigNode, LayeredConfigTree, load_yaml
