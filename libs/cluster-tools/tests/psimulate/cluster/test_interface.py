import socket

import pytest

from vivarium_cluster_tools.psimulate.cluster import validate_cluster_environment


@pytest.fixture(
    params=[
        "gen-slurm-sarchive-p0051",
        "long-slurm-sarchive-p0022",
    ]
)
def good_host(request):
    return request.param


@pytest.fixture(
    params=[
        "gen-slurm-slogin-p01" "gen-uge-submit-p01",
        "-slogin-",
    ]
)
def bad_host(request):
    return request.param


def test_validate_cluster_environment_pass(monkeypatch, good_host):
    monkeypatch.setattr(socket, "gethostname", lambda: good_host)
    validate_cluster_environment()


def test_validate_cluster_environment_fail(monkeypatch, bad_host):
    monkeypatch.setattr(socket, "gethostname", lambda: bad_host)
    with pytest.raises(RuntimeError):
        validate_cluster_environment()
