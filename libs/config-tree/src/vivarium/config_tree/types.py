from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

if TYPE_CHECKING:
    from layered_config_tree import LayeredConfigTree

# Data input types
InputData = Union[dict[str, Any], str, Path, "LayeredConfigTree"]
