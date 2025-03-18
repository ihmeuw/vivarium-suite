"""
=========
Utilities
=========

This module contains utility functions and classes for the layered_config_tree
package.

"""

from collections.abc import Hashable
from pathlib import Path
from typing import Any

import yaml

from layered_config_tree import DuplicatedConfigurationError


def load_yaml(data: str | Path) -> dict[str, Any]:
    """Loads a YAML filepath or string into a dictionary.

    Parameters
    ----------
    data
        The YAML content to load. This can be a file path to a YAML file or a string
        containing YAML-formatted text.

    Raises
    ------
    ValueError
        If the loaded YAML content is not a dictionary.

    Returns
    -------
        A dictionary representation of the loaded YAML content.

    Notes
    -----
    If `data` is a Path object or a string that ends with ".yaml" or ".yml", it is
    treated as a filepath and this function loads the file. Otherwise, `data` is a
    string that does _not_ end in ".yaml" or ".yml" and it is treated as YAML-formatted
    text which is loaded directly into a dictionary.
    """

    if (isinstance(data, str) and data.endswith((".yaml", ".yml"))) or isinstance(data, Path):
        # 'data' is a filepath to a yaml file (rather than a yaml string)
        with open(data) as f:
            data = f.read()
    data_dict: dict[str, Any] = yaml.load(data, Loader=SafeLoader)

    if not isinstance(data_dict, dict):
        raise ValueError(
            f"Loaded yaml file {data_dict} should be a dictionary but is type {type(data_dict)}"
        )

    return data_dict


class SafeLoader(yaml.SafeLoader):
    """A yaml.SafeLoader that restricts duplicate keys."""

    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[Hashable, Any]:
        """Constructs the standard mapping after checking for duplicates.

        Raises
        ------
        DuplicateKeysInYAMLError
            If duplicate keys within the same level are detected in the YAML file
            being loaded.

        Notes
        -----
        A key is considered a duplicate only if it is the same as another key
        _at the same level in the YAML_.

        This raises upon the _first_ duplicate key found; other duplicates may exist
        (in which case a new error will be raised upon re-loading of the YAML file
        once the duplicate is resolved).
        """
        mapping = []
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)  # type: ignore[no-untyped-call]
            if key in mapping:
                raise DuplicatedConfigurationError(
                    f"Duplicate key detected at same level of YAML: {key}. Resolve duplicates and try again.",
                    name=f"duplicated_{key}",
                    layer=None,
                    source=None,
                    value=None,
                )
            mapping.append(key)
        return super().construct_mapping(node, deep)
