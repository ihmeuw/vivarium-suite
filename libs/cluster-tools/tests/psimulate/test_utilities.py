import os
import shutil
import time
from pathlib import Path
from subprocess import PIPE, Popen
from typing import Any, TypedDict

import pytest
from pytest import FixtureRequest

from vivarium_cluster_tools.utilities import backoff_and_retry, mkdir


class MkdirParams(TypedDict, total=False):
    """Parameters for the mkdir function."""

    parents: bool
    exists_ok: bool
    umask: int


@pytest.fixture(
    params=[
        ({}, None),
        ({"parents": False}, None),
        ({"parents": True}, None),
        ({"exists_ok": False, "parents": True}, None),
        ({"umask": 0o013, "parents": True}, "drwxrw-r--"),
        ({"parents": True}, None),
        ({"exists_ok": True, "parents": True}, None),
    ]
)
def permissions_params(request: FixtureRequest) -> tuple[MkdirParams, str | None]:
    return request.param  # type: ignore[no-any-return]


def test_mkdir_set_permissions(permissions_params: tuple[MkdirParams, str | None]) -> None:
    # Get prior umask value
    prior_umask = os.umask(0)
    os.umask(prior_umask)

    cwd = Path(os.getcwd())
    parent_dir_name = "parent_dir"
    child_dir_name = "child_dir"

    parent_path = cwd / parent_dir_name
    path = parent_path / child_dir_name

    mkdir_params: MkdirParams = permissions_params[0]
    permissions: str | None = permissions_params[1] if permissions_params[1] else "drwxrwxr-x"

    def test_mkdir_permissions() -> None:
        mkdir(path, **mkdir_params)
        proc = Popen(
            f"ls -l | grep '{parent_dir_name}' | grep '{permissions}'",
            shell=True,
            stdout=PIPE,
        )
        assert proc.communicate()[0], "Parent directory has incorrect permissions"
        proc = Popen(
            f"ls -l '{parent_dir_name}' | grep '{child_dir_name}' | grep '{permissions}'",
            shell=True,
            stdout=PIPE,
        )
        assert proc.communicate()[0], "Child directory has incorrect permissions"

    try:
        if "parents" not in mkdir_params or not mkdir_params["parents"]:
            with pytest.raises(FileNotFoundError):
                mkdir(path, **mkdir_params)
        else:
            test_mkdir_permissions()

            # Setting new umask doesn't change permissions of existing directories if they exist
            mkdir_params["umask"] = 0o003
            if "exists_ok" not in mkdir_params or not mkdir_params["exists_ok"]:
                with pytest.raises(FileExistsError):
                    mkdir(path, **mkdir_params)
            else:
                test_mkdir_permissions()

    finally:
        if parent_path.exists():
            shutil.rmtree(parent_path)

        assert prior_umask == os.umask(prior_umask), "umask was changed and not reset"


def test_backoff_and_retry() -> None:
    class WarningCatcher:
        def __init__(self) -> None:
            self.caught_warnings: list[str] = []

        def warn(self, message: str, *args: Any, **kwargs: Any) -> None:
            self.caught_warnings.append(message)

    wc = WarningCatcher()

    @backoff_and_retry(log_function=wc.warn)
    def successful_function() -> bool:
        return True

    assert successful_function()
    assert len(wc.caught_warnings) == 0

    wc = WarningCatcher()

    @backoff_and_retry(backoff_seconds=0.1, num_retries=5, log_function=wc.warn)
    def failing_function() -> None:
        raise ValueError

    start = time.time()
    with pytest.raises(ValueError):
        failing_function()
    duration = time.time() - start
    # 5 for the retries, 1 for the final fail
    assert len(wc.caught_warnings) == 5 + 1
    assert abs(duration - 0.1 * 5) < 0.01
