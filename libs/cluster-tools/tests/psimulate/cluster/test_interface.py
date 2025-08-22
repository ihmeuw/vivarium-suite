import socket
from typing import Any

import pytest

from vivarium_cluster_tools.psimulate.cluster import validate_cluster_environment


@pytest.fixture(
    params=[
        "gen-slurm-sarchive-p0051",
        "long-slurm-sarchive-p0022",
    ]
)
def good_host(request: pytest.FixtureRequest) -> str:
    # request.param can typically be Any, but for this test we need a str
    if not isinstance(request.param, str):
        raise TypeError(f"Expected str, got {type(request.param)}")
    return request.param


@pytest.fixture(
    params=[
        "gen-slurm-slogin-p01" "gen-uge-submit-p01",
        "-slogin-",
    ]
)
def bad_host(request: pytest.FixtureRequest) -> str:
    # request.param can typically be Any, but for this test we need a str
    if not isinstance(request.param, str):
        raise TypeError(f"Expected str, got {type(request.param)}")
    return request.param


def test_validate_cluster_environment_pass(
    monkeypatch: pytest.MonkeyPatch, good_host: str
) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: good_host)
    validate_cluster_environment()


def test_validate_cluster_environment_fail(
    monkeypatch: pytest.MonkeyPatch, bad_host: str
) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: bad_host)
    with pytest.raises(RuntimeError):
        validate_cluster_environment()
