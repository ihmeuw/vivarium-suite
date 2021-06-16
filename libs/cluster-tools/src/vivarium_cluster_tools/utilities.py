"""
================================
vivarium_cluster_tools Utilities
================================

Making directories is hard.

"""
import os
from pathlib import Path
from typing import Union


def mkdir(path: Union[str, Path], umask: int = 0o002, exists_ok: bool = False, parents: bool = False) -> None:
    """
    Utility method to create a directory with specified permissions
    :param path: path of the directory to create
    :param umask: umask specifying the desired permissions - defaults to 0o002
    :param exists_ok: if False, raises FileExistsError if the directory already exists
    :param parents: if False, raises FileNotFoundError if the directory's parent doesn't exist
    """
    path = Path(path)
    old_umask = os.umask(umask)
    try:
        path.mkdir(exist_ok=exists_ok, parents=parents)
    finally:
        os.umask(old_umask)
