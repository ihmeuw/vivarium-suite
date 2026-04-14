"""
=====
Types
=====

Type aliases used across the layered_config_tree package.

"""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

if TYPE_CHECKING:
    from layered_config_tree import LayeredConfigTree

# Accepted input data types for :class:`~layered_config_tree.LayeredConfigTree`
# operations. Can be a dictionary, a YAML string, a file path, or another
# :class:`~layered_config_tree.LayeredConfigTree`.
InputData = Union[dict[str, Any], str, Path, "LayeredConfigTree"]
