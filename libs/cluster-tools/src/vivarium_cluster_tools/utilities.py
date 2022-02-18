"""
================================
vivarium_cluster_tools Utilities
================================

Making directories is hard.

"""
import functools
import os
import time
import warnings
from pathlib import Path
from typing import Callable, Union


def mkdir(
    path: Union[str, Path], umask: int = 0o002, exists_ok: bool = False, parents: bool = False
) -> None:
    """Utility method to create a directory with specified permissions.

    Parameters
    ----------
    path
        Path of the directory to create.
    umask
        Umask specifying the desired permissions - defaults to 0o002.
    exists_ok
        If False, raises FileExistsError if the directory already exists.
    parents
        If False, raises FileNotFoundError if the directory's parent doesn't exist.

    """
    path = Path(path)
    old_umask = os.umask(umask)
    try:
        path.mkdir(exist_ok=exists_ok, parents=parents)
    finally:
        os.umask(old_umask)


def backoff_and_retry(
    backoff_seconds: Union[int, float] = 30,
    num_retries: int = 3,
    log_function: Callable[[str], None] = warnings.warn,
):
    """Adds a retry handler to the decorated function.

    Parameters
    ----------
    backoff_seconds
        Number of seconds to wait until retrying the operation.
    num_retries
        Number of times to retry before failing.
    log_function
        A callable used to emit retry messages.  Defaults to emitting
        a standard library warning.

    """

    def _wrap(func):
        @functools.wraps(func)
        def _wrapped(*args, **kwargs):
            retries = num_retries
            while retries:
                try:
                    result = func(*args, **kwargs)
                    break
                except Exception as e:
                    log_function(
                        f"Error trying to write results to hdf, retries remaining {retries}"
                    )
                    time.sleep(backoff_seconds)
                    retries -= 1
                    if not retries:
                        log_function(f"Retries exhausted.")
                        raise e
            return result

        return _wrapped

    return _wrap
