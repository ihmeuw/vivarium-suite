import os
import shutil
from pathlib import Path
from subprocess import PIPE, Popen
from typing import Dict, List, Optional

import pytest

from vivarium_cluster_tools.psimulate.utilities import exit_if_on_submit_host
from vivarium_cluster_tools.utilities import mkdir


@pytest.fixture
def hostname_init():
    return {
        "good": [
            "user@int-uge-archive-p006",
        ],
        "bad": [
            "user@gen-uge-submit-p01",
            "-submit-",
            "user@gen-uge-submit-p02",
        ],
    }


def test_exit_if_on_submit_host(hostname_init):
    for host in hostname_init["bad"]:
        with pytest.raises(RuntimeError) as _:
            exit_if_on_submit_host(host)
    for host in hostname_init["good"]:
        assert not exit_if_on_submit_host(host)


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
def permissions_params(request):
    return request.param


def test_mkdir_set_permissions(permissions_params: List) -> None:
    # Get prior umask value
    prior_umask = os.umask(0)
    os.umask(prior_umask)

    cwd = Path(os.getcwd())
    parent_dir_name = "parent_dir"
    child_dir_name = "child_dir"

    parent_path = cwd / parent_dir_name
    path = parent_path / child_dir_name

    mkdir_params: Dict = permissions_params[0]
    permissions: Optional[str] = (
        permissions_params[1] if permissions_params[1] else "drwxrwxr-x"
    )

    def test_mkdir_permissions():
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
