from layered_config_tree.__about__ import (
    __author__,
    __copyright__,
    __email__,
    __license__,
    __summary__,
    __title__,
    __uri__,
)

# FIXME: Is there a better way to get around mypy error
# "error: Module "layered_config_tree" does not explicitly export attribute "ConfigurationKeyError"  [attr-defined]"
__all__ = [
    "ConfigNode",
    "ConfigurationError",
    "ConfigurationKeyError",
    "DuplicatedConfigurationError",
    "LayeredConfigTree",
]

from layered_config_tree._version import __version__
from layered_config_tree.exceptions import (
    ConfigurationError,
    ConfigurationKeyError,
    DuplicatedConfigurationError,
)
from layered_config_tree.main import ConfigNode, LayeredConfigTree
