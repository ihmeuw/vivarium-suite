"""
=====
Types
=====

Type aliases used across the vivarium.config_tree package.

"""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

if TYPE_CHECKING:
    from vivarium.config_tree import ConfigTree

# Accepted input data types for :class:`~vivarium.config_tree.ConfigTree`
# operations. Can be a dictionary, a YAML string, a file path, or another
# :class:`~vivarium.config_tree.ConfigTree`.
InputData = Union[dict[str, Any], str, Path, "ConfigTree"]
